"""Automatic setup and installation of LitmusChaos and dependencies."""

import json as _json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class LitmusSetup:
    """Handles automatic installation and verification of LitmusChaos."""

    LITMUS_NAMESPACE = "litmus"
    LITMUS_CRD_GROUP = "litmuschaos.io"
    KUBESPRAY_REPO = "https://github.com/kubernetes-sigs/kubespray.git"
    KUBESPRAY_VERSION = "v2.24.0"
    KUBESPRAY_DIR = Path.home() / ".chaosprobe" / "kubespray"
    VAGRANT_DIR = Path.home() / ".chaosprobe" / "vagrant"

    # ChaosCenter (dashboard) constants
    CHAOSCENTER_HELM_CHART = "litmuschaos/litmus"
    CHAOSCENTER_RELEASE_NAME = "chaos"
    CHAOSCENTER_FRONTEND_SVC = "chaos-litmus-frontend-service"
    CHAOSCENTER_SERVER_SVC = "chaos-litmus-server-service"
    CHAOSCENTER_AUTH_SVC = "chaos-litmus-auth-server-service"
    CHAOSCENTER_FRONTEND_PORT = 9091
    CHAOSCENTER_SERVER_PORT = 9002
    CHAOSCENTER_DEFAULT_USER = "admin"
    CHAOSCENTER_DEFAULT_PASS = "litmus"

    VAGRANTFILE_TEMPLATE = """# -*- mode: ruby -*-
# vi: set ft=ruby :

# ChaosProbe Vagrant Configuration
# Auto-generated - do not edit directly

CLUSTER_NAME = "{cluster_name}"
NUM_CONTROL_PLANES = {num_control_planes}
NUM_WORKERS = {num_workers}
CP_MEMORY = {cp_memory}
CP_CPUS = {cp_cpus}
WORKER_MEMORY = {worker_memory}
WORKER_CPUS = {worker_cpus}
BOX_IMAGE = "{box_image}"
NETWORK_PREFIX = "{network_prefix}"

Vagrant.configure("2") do |config|
  config.vm.box = BOX_IMAGE
  config.ssh.insert_key = false

  # Control plane nodes
  (1..NUM_CONTROL_PLANES).each do |i|
    config.vm.define "cp#{{i}}" do |node|
      node.vm.hostname = "cp#{{i}}"
      node.vm.network "private_network", ip: "#{{NETWORK_PREFIX}}.#{{10 + i}}"

      node.vm.provider "virtualbox" do |vb|
        vb.name = "#{{CLUSTER_NAME}}-cp#{{i}}"
        vb.memory = CP_MEMORY
        vb.cpus = CP_CPUS
        vb.customize ["modifyvm", :id, "--natdnshostresolver1", "on"]
      end

      node.vm.provider "libvirt" do |lv|
        lv.memory = CP_MEMORY
        lv.cpus = CP_CPUS
      end

      # Enable password-less sudo
      node.vm.provision "shell", inline: <<-SHELL
        echo "vagrant ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vagrant
        chmod 440 /etc/sudoers.d/vagrant
      SHELL
    end
  end

  # Worker nodes
  (1..NUM_WORKERS).each do |i|
    config.vm.define "worker#{{i}}" do |node|
      node.vm.hostname = "worker#{{i}}"
      node.vm.network "private_network", ip: "#{{NETWORK_PREFIX}}.#{{20 + i}}"

      node.vm.provider "virtualbox" do |vb|
        vb.name = "#{{CLUSTER_NAME}}-worker#{{i}}"
        vb.memory = WORKER_MEMORY
        vb.cpus = WORKER_CPUS
        vb.customize ["modifyvm", :id, "--natdnshostresolver1", "on"]
      end

      node.vm.provider "libvirt" do |lv|
        lv.memory = WORKER_MEMORY
        lv.cpus = WORKER_CPUS
      end

      # Enable password-less sudo
      node.vm.provision "shell", inline: <<-SHELL
        echo "vagrant ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vagrant
        chmod 440 /etc/sudoers.d/vagrant
      SHELL
    end
  end
end
"""

    def __init__(self, skip_k8s_init: bool = False):
        """Initialize the setup handler.

        Args:
            skip_k8s_init: Skip Kubernetes client initialization (for pre-cluster setup).
        """
        self._in_cluster = False
        self._k8s_initialized = False

        if not skip_k8s_init:
            self._init_k8s_client()

    def _init_k8s_client(self):
        """Initialize Kubernetes client."""
        try:
            config.load_incluster_config()
            self._in_cluster = True
        except config.ConfigException:
            try:
                config.load_kube_config()
            except config.ConfigException:
                return

        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()
        self.apiext_api = client.ApiextensionsV1Api()
        self.rbac_api = client.RbacAuthorizationV1Api()
        self.storage_api = client.StorageV1Api()
        self._k8s_initialized = True

    def get_cluster_info(self) -> dict:
        """Get information about the current cluster context.

        Returns:
            Dictionary with cluster information.
        """
        info = {
            "in_cluster": self._in_cluster,
            "context": None,
            "cluster": None,
            "server": None,
            "is_local": False,
        }

        try:
            _, active_context = config.list_kube_config_contexts()
            if active_context:
                info["context"] = active_context.get("name")
                info["cluster"] = active_context.get("context", {}).get("cluster")

            # Try to get server URL
            result = subprocess.run(
                [
                    "kubectl",
                    "config",
                    "view",
                    "--minify",
                    "-o",
                    "jsonpath={.clusters[0].cluster.server}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                server = result.stdout.strip()
                info["server"] = server
                # Check if it's a local cluster
                local_indicators = [
                    "localhost",
                    "127.0.0.1",
                    "0.0.0.0",
                    "minikube",
                    "kind",
                    "k3s",
                    "k3d",
                    "docker-desktop",
                ]
                info["is_local"] = any(ind in server.lower() for ind in local_indicators)

        except Exception:
            pass

        return info

    def validate_cluster(self) -> tuple[bool, str]:
        """Validate that we're connected to a Kubernetes cluster.

        Returns:
            Tuple of (is_valid, message).
        """
        info = self.get_cluster_info()

        # No cluster access at all
        if not info["server"] and not self._k8s_initialized:
            return False, (
                "No Kubernetes cluster configured.\n"
                "Use 'chaosprobe cluster create' to deploy a cluster with Kubespray,\n"
                "or configure kubectl to connect to an existing cluster."
            )

        # Quick kubectl check (fail fast) to avoid long TCP timeouts
        try:
            subprocess.run(
                ["kubectl", "cluster-info"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            return False, (
                "kubectl timed out after 10s while contacting the API server.\n"
                "The cluster may be stopped or unreachable. Try these steps:\n"
                "  1. Check if VMs are running:  virsh list --all\n"
                "  2. Start stopped VMs:         virsh start <vm-name>\n"
                "  3. Wait ~30s, then verify:    kubectl cluster-info\n"
                "  4. If using Vagrant:           chaosprobe cluster vagrant up --provider=libvirt"
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # kubectl missing or returned error — fall back to client checks below
            pass

        # Initialize k8s client if needed to test connectivity
        if not self._k8s_initialized:
            self._init_k8s_client()

        if not self._k8s_initialized:
            return False, "Could not initialize Kubernetes client."

        try:
            # Use a short request timeout so we fail fast on unreachable API servers
            self.core_api.list_namespace(_request_timeout=5)
            return True, f"Connected to: {info['context']} ({info['server']})"
        except ApiException as e:
            return False, f"Cluster access error: {getattr(e, 'reason', str(e))}"
        except Exception as e:
            return False, f"Cluster unreachable at {info['server']}: {e}"

    # -------------------------------------------------------------------------
    # Kubespray cluster management
    # -------------------------------------------------------------------------

    def _check_ansible(self) -> bool:
        """Check if ansible is available."""
        try:
            subprocess.run(
                ["ansible", "--version"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_python_venv(self) -> bool:
        """Check if python venv module is available."""
        try:
            subprocess.run(
                ["python3", "-m", "venv", "--help"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _ensure_kubespray(self) -> Path:
        """Ensure kubespray is cloned and dependencies are installed.

        Returns:
            Path to the kubespray directory.
        """
        kubespray_dir = self.KUBESPRAY_DIR

        if not kubespray_dir.exists():
            print(f"Cloning Kubespray {self.KUBESPRAY_VERSION}...")
            kubespray_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    self.KUBESPRAY_VERSION,
                    self.KUBESPRAY_REPO,
                    str(kubespray_dir),
                ],
                check=True,
            )

        # Create/update venv with kubespray requirements
        venv_dir = kubespray_dir / "venv"
        if not venv_dir.exists():
            print("Creating Python virtual environment for Kubespray...")
            subprocess.run(
                ["python3", "-m", "venv", str(venv_dir)],
                check=True,
            )

            print("Installing Kubespray dependencies...")
            pip_path = venv_dir / "bin" / "pip"
            subprocess.run(
                [str(pip_path), "install", "-U", "pip"],
                check=True,
            )
            subprocess.run(
                [str(pip_path), "install", "-r", str(kubespray_dir / "requirements.txt")],
                check=True,
            )

        return kubespray_dir

    def generate_inventory(
        self,
        hosts: list[dict],
        cluster_name: str = "chaosprobe",
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Generate Kubespray inventory from host definitions.

        Args:
            hosts: List of host dictionaries with keys:
                - name: Host name
                - ip: IP address
                - ansible_host: SSH address (optional, defaults to ip)
                - ansible_user: SSH user (optional, defaults to root)
                - roles: List of roles (control_plane, worker)
            cluster_name: Name for the cluster inventory.
            output_dir: Output directory for inventory (optional).

        Returns:
            Path to the generated inventory directory.
        """
        kubespray_dir = self._ensure_kubespray()

        if output_dir is None:
            output_dir = kubespray_dir / "inventory" / cluster_name
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        # Copy sample inventory as base
        sample_dir = kubespray_dir / "inventory" / "sample"
        if sample_dir.exists():
            for item in ["group_vars"]:
                src = sample_dir / item
                dst = output_dir / item
                if src.exists() and not dst.exists():
                    shutil.copytree(src, dst)

        # Generate hosts.yaml
        inventory = {
            "all": {
                "hosts": {},
                "children": {
                    "kube_control_plane": {"hosts": {}},
                    "kube_node": {"hosts": {}},
                    "etcd": {"hosts": {}},
                    "k8s_cluster": {
                        "children": {
                            "kube_control_plane": {},
                            "kube_node": {},
                        }
                    },
                    "calico_rr": {"hosts": {}},
                },
            }
        }

        for host in hosts:
            name = host["name"]
            host_config = {
                "ansible_host": host.get("ansible_host", host["ip"]),
                "ip": host["ip"],
                "access_ip": host["ip"],
            }
            if "ansible_user" in host:
                host_config["ansible_user"] = host["ansible_user"]

            inventory["all"]["hosts"][name] = host_config

            roles = host.get("roles", ["worker"])
            if "control_plane" in roles:
                inventory["all"]["children"]["kube_control_plane"]["hosts"][name] = {}
                inventory["all"]["children"]["etcd"]["hosts"][name] = {}
            if "worker" in roles or "control_plane" in roles:
                inventory["all"]["children"]["kube_node"]["hosts"][name] = {}

        # Write hosts.yaml
        import yaml

        hosts_file = output_dir / "hosts.yaml"
        with open(hosts_file, "w") as f:
            yaml.dump(inventory, f, default_flow_style=False)

        print(f"Generated inventory at: {output_dir}")
        return output_dir

    def deploy_cluster(
        self,
        inventory_dir: Path,
        extra_vars: Optional[dict] = None,
        become_pass: Optional[str] = None,
    ) -> bool:
        """Deploy a Kubernetes cluster using Kubespray.

        Args:
            inventory_dir: Path to the inventory directory.
            extra_vars: Extra variables to pass to ansible.
            become_pass: Sudo password for ansible become.

        Returns:
            True if deployment succeeded.
        """
        kubespray_dir = self._ensure_kubespray()
        venv_dir = kubespray_dir / "venv"
        ansible_playbook = venv_dir / "bin" / "ansible-playbook"

        cmd = [
            str(ansible_playbook),
            "-i",
            str(inventory_dir / "hosts.yaml"),
            str(kubespray_dir / "cluster.yml"),
            "-b",  # become (sudo)
        ]

        if become_pass:
            cmd.extend(["--become-password", become_pass])

        if extra_vars:
            for key, value in extra_vars.items():
                cmd.extend(["-e", f"{key}={value}"])

        print("Deploying Kubernetes cluster with Kubespray...")
        print("  This typically takes 15-30 minutes...")
        print(f"  Running: {' '.join(cmd[:5])}...")

        try:
            subprocess.run(cmd, check=True, cwd=str(kubespray_dir))
            print("Cluster deployment complete!")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Cluster deployment failed: {e}") from e

    def fetch_kubeconfig(
        self,
        control_plane_host: str,
        ansible_user: str = "root",
        output_path: Optional[Path] = None,
        ssh_key: Optional[Path] = None,
    ) -> Path:
        """Fetch kubeconfig from the control plane node.

        Args:
            control_plane_host: IP or hostname of control plane.
            ansible_user: SSH user.
            output_path: Where to save kubeconfig.
            ssh_key: Path to SSH private key file (for Vagrant VMs).

        Returns:
            Path to the kubeconfig file.
        """
        if output_path is None:
            output_path = Path.home() / ".kube" / "config-chaosprobe"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Fetching kubeconfig from {control_plane_host}...")
        try:
            # Use ssh + sudo cat instead of scp, because admin.conf is
            # owned by root and the SSH user may not have read access.
            ssh_cmd = ["ssh"]
            if ssh_key:
                ssh_cmd.extend(["-i", str(ssh_key), "-o", "StrictHostKeyChecking=no"])
            ssh_cmd.append(f"{ansible_user}@{control_plane_host}")
            ssh_cmd.append("sudo cat /etc/kubernetes/admin.conf")
            result = subprocess.run(ssh_cmd, check=True, capture_output=True, text=True)
            output_path.write_text(result.stdout)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to fetch kubeconfig: {e}") from e

        # Update the server address in kubeconfig to use external IP
        with open(output_path) as f:
            kubeconfig_content = f.read()

        # Replace internal IP with control plane host IP
        kubeconfig_content = kubeconfig_content.replace(
            "server: https://127.0.0.1:", f"server: https://{control_plane_host}:"
        )

        with open(output_path, "w") as f:
            f.write(kubeconfig_content)

        print(f"Kubeconfig saved to: {output_path}")
        print(f"Use: export KUBECONFIG={output_path}")
        return output_path

    def destroy_cluster(
        self,
        inventory_dir: Path,
        become_pass: Optional[str] = None,
    ) -> bool:
        """Destroy a Kubernetes cluster using Kubespray reset playbook.

        Args:
            inventory_dir: Path to the inventory directory.
            become_pass: Sudo password for ansible become.

        Returns:
            True if destruction succeeded.
        """
        kubespray_dir = self._ensure_kubespray()
        venv_dir = kubespray_dir / "venv"
        ansible_playbook = venv_dir / "bin" / "ansible-playbook"

        cmd = [
            str(ansible_playbook),
            "-i",
            str(inventory_dir / "hosts.yaml"),
            str(kubespray_dir / "reset.yml"),
            "-b",
            "-e",
            "reset_confirmation=yes",
        ]

        if become_pass:
            cmd.extend(["--become-password", become_pass])

        print("Destroying Kubernetes cluster...")
        try:
            subprocess.run(cmd, check=True, cwd=str(kubespray_dir))
            print("Cluster destroyed!")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Cluster destruction failed: {e}") from e

    # -------------------------------------------------------------------------
    # Vagrant VM management
    # -------------------------------------------------------------------------

    def _check_vagrant(self) -> bool:
        """Check if Vagrant is available."""
        try:
            subprocess.run(
                ["vagrant", "--version"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_libvirt(self) -> dict:
        """Check if libvirt/KVM is available and properly configured.

        Returns:
            Dictionary with status of libvirt components.
        """
        result = {
            "kvm_available": False,
            "libvirtd_installed": False,
            "libvirtd_running": False,
            "user_in_groups": False,
            "vagrant_libvirt_plugin": False,
            "all_ready": False,
        }

        # Check if KVM is available
        result["kvm_available"] = Path("/dev/kvm").exists()

        # Check if libvirtd is installed
        try:
            subprocess.run(
                ["which", "libvirtd"],
                check=True,
                capture_output=True,
            )
            result["libvirtd_installed"] = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Check if libvirtd is running
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "libvirtd"],
                capture_output=True,
                text=True,
            )
            result["libvirtd_running"] = proc.stdout.strip() == "active"
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Try service command for WSL
            try:
                proc = subprocess.run(
                    ["service", "libvirtd", "status"],
                    capture_output=True,
                    text=True,
                )
                result["libvirtd_running"] = proc.returncode == 0
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass

        # Check if user is in libvirt and kvm groups
        try:
            proc = subprocess.run(
                ["groups"],
                capture_output=True,
                text=True,
            )
            groups = proc.stdout.strip().split()
            result["user_in_groups"] = "libvirt" in groups and "kvm" in groups
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Check if vagrant-libvirt plugin is installed
        try:
            proc = subprocess.run(
                ["vagrant", "plugin", "list"],
                capture_output=True,
                text=True,
            )
            result["vagrant_libvirt_plugin"] = "vagrant-libvirt" in proc.stdout
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        result["all_ready"] = all(
            [
                result["kvm_available"],
                result["libvirtd_installed"],
                result["libvirtd_running"],
                result["user_in_groups"],
                result["vagrant_libvirt_plugin"],
            ]
        )

        return result

    def install_libvirt(self, skip_service_start: bool = False) -> dict:
        """Install libvirt and related packages for Vagrant libvirt provider.

        This installs:
        - qemu-kvm, libvirt-daemon-system, libvirt-clients, bridge-utils, virtinst
        - Adds user to libvirt and kvm groups
        - Starts libvirtd service
        - Installs vagrant-libvirt plugin

        Args:
            skip_service_start: Skip starting libvirtd service (for testing).

        Returns:
            Dictionary with installation status.
        """
        import getpass

        current_user = getpass.getuser()

        result = {
            "packages_installed": False,
            "groups_added": False,
            "service_started": False,
            "plugin_installed": False,
            "needs_relogin": False,
        }

        # Check KVM availability first
        if not Path("/dev/kvm").exists():
            raise RuntimeError(
                "KVM is not available. Make sure:\n"
                "  - CPU virtualization is enabled in BIOS\n"
                "  - For WSL2: Enable nested virtualization in .wslconfig"
            )

        # Install libvirt packages
        print("Installing libvirt packages (requires sudo)...")
        packages = [
            "qemu-kvm",
            "libvirt-daemon-system",
            "libvirt-clients",
            "bridge-utils",
            "virtinst",
            "libvirt-dev",  # Required for vagrant-libvirt plugin
        ]

        try:
            # Update apt first
            subprocess.run(
                ["sudo", "apt", "update"],
                check=True,
            )
            # Install packages
            subprocess.run(
                ["sudo", "apt", "install", "-y"] + packages,
                check=True,
            )
            result["packages_installed"] = True
            print("  Packages installed successfully")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install packages: {e}") from e

        # Add user to groups
        print(f"Adding user '{current_user}' to libvirt and kvm groups...")
        try:
            subprocess.run(
                ["sudo", "usermod", "-aG", "libvirt", current_user],
                check=True,
            )
            subprocess.run(
                ["sudo", "usermod", "-aG", "kvm", current_user],
                check=True,
            )
            result["groups_added"] = True
            result["needs_relogin"] = True
            print("  Groups added successfully")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to add user to groups: {e}") from e

        # Start libvirtd service
        if not skip_service_start:
            print("Starting libvirtd service...")
            try:
                # Try systemctl first
                subprocess.run(
                    ["sudo", "systemctl", "start", "libvirtd"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["sudo", "systemctl", "enable", "libvirtd"],
                    check=True,
                    capture_output=True,
                )
                result["service_started"] = True
                print("  Service started successfully")
            except subprocess.CalledProcessError:
                # Try service command for WSL
                try:
                    subprocess.run(
                        ["sudo", "service", "libvirtd", "start"],
                        check=True,
                    )
                    result["service_started"] = True
                    print("  Service started successfully")
                except subprocess.CalledProcessError as e:
                    print(f"  Warning: Could not start libvirtd: {e}")
                    print("  You may need to start it manually: sudo service libvirtd start")

        # Install vagrant-libvirt plugin
        print("Installing vagrant-libvirt plugin...")
        try:
            subprocess.run(
                ["vagrant", "plugin", "install", "vagrant-libvirt"],
                check=True,
            )
            result["plugin_installed"] = True
            print("  Plugin installed successfully")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install vagrant-libvirt plugin: {e}") from e

        return result

    def create_vagrantfile(
        self,
        cluster_name: str = "chaosprobe",
        num_control_planes: int = 1,
        num_workers: int = 2,
        vm_memory: int | None = None,
        vm_cpus: int | None = None,
        cp_memory: int = 4096,
        cp_cpus: int = 2,
        worker_memory: int = 4096,
        worker_cpus: int = 2,
        box_image: str = "generic/ubuntu2204",
        network_prefix: str = "192.168.56",
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Create a Vagrantfile for local cluster VMs.

        Args:
            cluster_name: Name for the cluster.
            num_control_planes: Number of control plane nodes.
            num_workers: Number of worker nodes.
            vm_memory: Legacy shorthand — sets both cp_memory and worker_memory.
            vm_cpus: Legacy shorthand — sets both cp_cpus and worker_cpus.
            cp_memory: Memory for control plane VMs in MB.
            cp_cpus: CPUs for control plane VMs.
            worker_memory: Memory for worker VMs in MB.
            worker_cpus: CPUs for worker VMs.
            box_image: Vagrant box image to use.
            network_prefix: Network prefix for private IPs (e.g., 192.168.56).
            output_dir: Directory to create Vagrantfile in.

        Returns:
            Path to the created Vagrantfile directory.
        """
        # Legacy single-value overrides both roles
        if vm_memory is not None:
            cp_memory = vm_memory
            worker_memory = vm_memory
        if vm_cpus is not None:
            cp_cpus = vm_cpus
            worker_cpus = vm_cpus

        if output_dir is None:
            output_dir = self.VAGRANT_DIR / cluster_name
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        vagrantfile_content = self.VAGRANTFILE_TEMPLATE.format(
            cluster_name=cluster_name,
            num_control_planes=num_control_planes,
            num_workers=num_workers,
            cp_memory=cp_memory,
            cp_cpus=cp_cpus,
            worker_memory=worker_memory,
            worker_cpus=worker_cpus,
            box_image=box_image,
            network_prefix=network_prefix,
        )

        vagrantfile_path = output_dir / "Vagrantfile"
        with open(vagrantfile_path, "w") as f:
            f.write(vagrantfile_content)

        print(f"Created Vagrantfile at: {output_dir}")
        return output_dir

    def provision_from_cluster_config(
        self,
        cluster_config: dict,
        cluster_name: str = "chaosprobe",
        provider: str = "libvirt",
    ) -> Path:
        """Provision a cluster from a scenario's cluster configuration.

        Reads node specs from the scenario's cluster config and creates/starts
        Vagrant VMs with those specs.

        Args:
            cluster_config: Cluster configuration dict with keys:
                - control_plane: {cpu, memory} (optional, defaults apply)
                - workers: {count, cpu, memory, disk}
                - provider: Optional provider override
            cluster_name: Name for the cluster.
            provider: Vagrant provider to use (overridden by cluster_config).

        Returns:
            Path to the Vagrant directory.
        """
        cp = cluster_config.get("control_plane", {})
        workers = cluster_config.get("workers", {})
        num_workers = workers.get("count", 2)
        config_provider = cluster_config.get("provider", provider)

        vagrant_dir = self.create_vagrantfile(
            cluster_name=cluster_name,
            num_control_planes=1,
            num_workers=num_workers,
            cp_memory=cp.get("memory", 4096),
            cp_cpus=cp.get("cpu", 2),
            worker_memory=workers.get("memory", 4096),
            worker_cpus=workers.get("cpu", 2),
        )

        print(
            f"Provisioning cluster from scenario config: "
            f"CP {cp.get('cpu', 2)} CPU / {cp.get('memory', 4096)}MB, "
            f"{num_workers} workers {workers.get('cpu', 2)} CPU / "
            f"{workers.get('memory', 4096)}MB"
        )

        self.vagrant_up(vagrant_dir, provider=config_provider)
        return vagrant_dir

    def vagrant_up(
        self,
        vagrant_dir: Path,
        provider: str = "virtualbox",
    ) -> bool:
        """Start Vagrant VMs.

        Args:
            vagrant_dir: Directory containing the Vagrantfile.
            provider: Vagrant provider (virtualbox, libvirt).

        Returns:
            True if VMs started successfully.
        """
        vagrant_dir = Path(vagrant_dir)

        if not (vagrant_dir / "Vagrantfile").exists():
            raise RuntimeError(f"No Vagrantfile found in {vagrant_dir}")

        print(f"Starting Vagrant VMs with provider: {provider}...")

        try:
            subprocess.run(
                ["vagrant", "up", f"--provider={provider}"],
                check=True,
                cwd=str(vagrant_dir),
            )
            print("Vagrant VMs started successfully!")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to start Vagrant VMs: {e}") from e

    def vagrant_destroy(self, vagrant_dir: Path, force: bool = False) -> bool:
        """Destroy Vagrant VMs.

        Args:
            vagrant_dir: Directory containing the Vagrantfile.
            force: Force destroy without confirmation.

        Returns:
            True if VMs destroyed successfully.
        """
        vagrant_dir = Path(vagrant_dir)

        if not (vagrant_dir / "Vagrantfile").exists():
            raise RuntimeError(f"No Vagrantfile found in {vagrant_dir}")

        print("Destroying Vagrant VMs...")

        cmd = ["vagrant", "destroy"]
        if force:
            cmd.append("-f")

        try:
            subprocess.run(cmd, check=True, cwd=str(vagrant_dir))
            print("Vagrant VMs destroyed successfully!")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to destroy Vagrant VMs: {e}") from e

    def _get_vagrant_env(self) -> dict:
        """Get environment variables for vagrant commands with libvirt support."""
        env = os.environ.copy()
        # Check if libvirt is available and set as default provider
        if self._check_libvirt().get("all_ready"):
            env["VAGRANT_DEFAULT_PROVIDER"] = "libvirt"
        return env

    def vagrant_status(self, vagrant_dir: Path) -> dict:
        """Get status of Vagrant VMs.

        Args:
            vagrant_dir: Directory containing the Vagrantfile.

        Returns:
            Dictionary with VM status information.
        """
        vagrant_dir = Path(vagrant_dir)

        if not (vagrant_dir / "Vagrantfile").exists():
            raise RuntimeError(f"No Vagrantfile found in {vagrant_dir}")

        try:
            result = subprocess.run(
                ["vagrant", "status", "--machine-readable"],
                capture_output=True,
                text=True,
                check=True,
                cwd=str(vagrant_dir),
                env=self._get_vagrant_env(),
            )

            vms = {}
            for line in result.stdout.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 4 and parts[2] == "state":
                    vm_name = parts[1]
                    state = parts[3]
                    vms[vm_name] = {"state": state}

            return vms
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to get Vagrant status: {e}") from e

    def get_vagrant_ssh_config(self, vagrant_dir: Path) -> list[dict]:
        """Get SSH configuration for all Vagrant VMs.

        Args:
            vagrant_dir: Directory containing the Vagrantfile.

        Returns:
            List of host dictionaries suitable for generate_inventory().
        """
        vagrant_dir = Path(vagrant_dir)

        if not (vagrant_dir / "Vagrantfile").exists():
            raise RuntimeError(f"No Vagrantfile found in {vagrant_dir}")

        # Get list of VMs
        status = self.vagrant_status(vagrant_dir)
        running_vms = [name for name, info in status.items() if info["state"] == "running"]

        if not running_vms:
            raise RuntimeError("No running Vagrant VMs found")

        hosts = []
        env = self._get_vagrant_env()
        for vm_name in running_vms:
            try:
                # Get SSH config for this VM
                result = subprocess.run(
                    ["vagrant", "ssh-config", vm_name],
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=str(vagrant_dir),
                    env=env,
                )

                ssh_config = {}
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if " " in line:
                        key, value = line.split(" ", 1)
                        ssh_config[key.lower()] = value

                # Get the private network IP from vagrant
                ip_result = subprocess.run(
                    ["vagrant", "ssh", vm_name, "-c", "hostname -I | awk '{print $2}'"],
                    capture_output=True,
                    text=True,
                    check=True,
                    cwd=str(vagrant_dir),
                    env=env,
                )
                private_ip = ip_result.stdout.strip()

                # Determine role based on VM name
                if vm_name.startswith("cp"):
                    roles = ["control_plane", "worker"]
                else:
                    roles = ["worker"]

                host = {
                    "name": vm_name,
                    "ip": private_ip,
                    "ansible_host": private_ip,
                    "ansible_user": ssh_config.get("user", "vagrant"),
                    "ansible_ssh_private_key_file": ssh_config.get("identityfile", "").strip('"'),
                    "roles": roles,
                }
                hosts.append(host)

            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to get SSH config for {vm_name}: {e}")
                continue

        return hosts

    def vagrant_fetch_kubeconfig(
        self,
        vagrant_dir: Path,
        output_path: Optional[Path] = None,
    ) -> Path:
        """Fetch kubeconfig from a Vagrant control plane VM.

        This auto-detects the SSH key and control plane IP from Vagrant.

        Args:
            vagrant_dir: Directory containing the Vagrantfile.
            output_path: Where to save kubeconfig.

        Returns:
            Path to the kubeconfig file.
        """
        vagrant_dir = Path(vagrant_dir)

        # Get host information from Vagrant
        hosts = self.get_vagrant_ssh_config(vagrant_dir)
        if not hosts:
            raise RuntimeError("No running Vagrant VMs found")

        # Find control plane host
        cp_hosts = [h for h in hosts if "control_plane" in h.get("roles", [])]
        if not cp_hosts:
            raise RuntimeError("No control plane VM found")

        cp_host = cp_hosts[0]
        control_plane_ip = cp_host["ip"]
        ssh_user = cp_host["ansible_user"]
        ssh_key = cp_host.get("ansible_ssh_private_key_file")

        if ssh_key:
            ssh_key = Path(ssh_key)

        return self.fetch_kubeconfig(
            control_plane_host=control_plane_ip,
            ansible_user=ssh_user,
            output_path=output_path,
            ssh_key=ssh_key,
        )

    def vagrant_deploy_cluster(
        self,
        vagrant_dir: Path,
        cluster_name: str = "chaosprobe",
    ) -> Path:
        """Deploy a Kubernetes cluster on Vagrant VMs.

        This is a convenience method that:
        1. Gets SSH config from Vagrant VMs
        2. Generates Kubespray inventory
        3. Deploys the cluster

        Args:
            vagrant_dir: Directory containing the Vagrantfile.
            cluster_name: Name for the cluster.

        Returns:
            Path to the inventory directory.
        """
        vagrant_dir = Path(vagrant_dir)

        # Get host information from Vagrant
        print("Getting VM information from Vagrant...")
        hosts = self.get_vagrant_ssh_config(vagrant_dir)

        if not hosts:
            raise RuntimeError("No running Vagrant VMs found")

        print(f"Found {len(hosts)} running VMs:")
        for host in hosts:
            print(f"  {host['name']} ({host['ip']}) - {', '.join(host['roles'])}")

        # Generate inventory
        inventory_dir = self.generate_inventory(hosts, cluster_name=cluster_name)

        # Add Vagrant-specific SSH settings to inventory
        hosts_file = inventory_dir / "hosts.yaml"
        import yaml

        with open(hosts_file) as f:
            inventory = yaml.safe_load(f)

        # Add SSH key path to each host
        for host in hosts:
            if host["name"] in inventory["all"]["hosts"]:
                if host.get("ansible_ssh_private_key_file"):
                    inventory["all"]["hosts"][host["name"]]["ansible_ssh_private_key_file"] = host[
                        "ansible_ssh_private_key_file"
                    ]
                inventory["all"]["hosts"][host["name"]][
                    "ansible_ssh_common_args"
                ] = "-o StrictHostKeyChecking=no"

        with open(hosts_file, "w") as f:
            yaml.dump(inventory, f, default_flow_style=False)

        # Deploy cluster
        self.deploy_cluster(inventory_dir)

        return inventory_dir

    # -------------------------------------------------------------------------
    # LitmusChaos management
    # -------------------------------------------------------------------------

    def is_litmus_installed(self) -> bool:
        """Check if LitmusChaos is installed in the cluster."""
        if not self._k8s_initialized:
            return False
        try:
            crds = self.apiext_api.list_custom_resource_definition()
            litmus_crds = [
                crd for crd in crds.items if crd.metadata.name.endswith(".litmuschaos.io")
            ]
            return len(litmus_crds) > 0
        except Exception:
            return False

    def is_litmus_ready(self) -> bool:
        """Check if LitmusChaos is ready and running."""
        if not self.is_litmus_installed():
            return False

        try:
            ns = self.core_api.read_namespace(self.LITMUS_NAMESPACE)
            if ns.status.phase != "Active":
                return False

            # Check for chaos-operator deployment (try different names)
            operator_names = ["litmus-chaos-operator", "chaos-operator", "litmus"]
            for name in operator_names:
                try:
                    dep = self.apps_api.read_namespaced_deployment(name, self.LITMUS_NAMESPACE)
                    if dep.status.ready_replicas == dep.spec.replicas:
                        return True
                except ApiException:
                    continue

            # Fall back to checking any deployment with chaos-operator in name
            deployments = self.apps_api.list_namespaced_deployment(self.LITMUS_NAMESPACE)
            for dep in deployments.items:
                if "operator" in dep.metadata.name.lower():
                    if dep.status.ready_replicas == dep.spec.replicas:
                        return True
            return False
        except Exception:
            return False

    def install_litmus(self, wait: bool = True, timeout: int = 180) -> bool:
        """Install LitmusChaos using Helm.

        Args:
            wait: Whether to wait for installation to complete.
            timeout: Timeout in seconds.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace(self.LITMUS_NAMESPACE)

        print("Adding LitmusChaos Helm repository...")
        try:
            subprocess.run(
                [
                    "helm",
                    "repo",
                    "add",
                    "litmuschaos",
                    "https://litmuschaos.github.io/litmus-helm/",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            pass  # Repo may already exist

        print("Updating Helm repositories...")
        try:
            subprocess.run(
                ["helm", "repo", "update"],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to update helm repos: {e}") from e

        # Install litmus-core chart (chaos operator and CRDs)
        print("Installing LitmusChaos operator...")
        try:
            subprocess.run(
                [
                    "helm",
                    "upgrade",
                    "--install",
                    "litmus",
                    "litmuschaos/litmus-core",
                    "--namespace",
                    self.LITMUS_NAMESPACE,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install LitmusChaos operator: {e}") from e

        # Install kubernetes-chaos chart (experiment definitions)
        print("Installing LitmusChaos experiments...")
        try:
            subprocess.run(
                [
                    "helm",
                    "upgrade",
                    "--install",
                    "chaos-experiments",
                    "litmuschaos/kubernetes-chaos",
                    "--namespace",
                    self.LITMUS_NAMESPACE,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install LitmusChaos experiments: {e}") from e

        if wait:
            return self._wait_for_litmus(timeout)

        return True

    def setup_rbac(self, namespace: str) -> bool:
        """Setup RBAC for running chaos experiments in a namespace.

        Args:
            namespace: Target namespace for chaos experiments.

        Returns:
            True if RBAC setup succeeded.
        """
        self._ensure_namespace(namespace)

        sa = client.V1ServiceAccount(
            metadata=client.V1ObjectMeta(
                name="litmus-admin",
                namespace=namespace,
                labels={"managed-by": "chaosprobe"},
            )
        )

        try:
            self.core_api.create_namespaced_service_account(namespace, sa)
        except ApiException as e:
            if e.status != 409:
                raise

        cluster_role = client.V1ClusterRole(
            metadata=client.V1ObjectMeta(
                name=f"litmus-admin-{namespace}",
                labels={"managed-by": "chaosprobe"},
            ),
            rules=[
                client.V1PolicyRule(
                    api_groups=[""],
                    resources=[
                        "pods",
                        "pods/log",
                        "pods/exec",
                        "events",
                        "services",
                        "configmaps",
                        "secrets",
                        "persistentvolumeclaims",
                        "nodes",
                    ],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["apps"],
                    resources=["deployments", "statefulsets", "replicasets", "daemonsets"],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["batch"],
                    resources=["jobs", "cronjobs"],
                    verbs=["get", "list", "watch", "create", "update", "patch", "delete"],
                ),
                client.V1PolicyRule(
                    api_groups=["litmuschaos.io"],
                    resources=["*"],
                    verbs=["*"],
                ),
            ],
        )

        try:
            self.rbac_api.create_cluster_role(cluster_role)
        except ApiException as e:
            if e.status == 409:
                self.rbac_api.replace_cluster_role(f"litmus-admin-{namespace}", cluster_role)
            else:
                raise

        cluster_role_binding = client.V1ClusterRoleBinding(
            metadata=client.V1ObjectMeta(
                name=f"litmus-admin-{namespace}-binding",
                labels={"managed-by": "chaosprobe"},
            ),
            subjects=[
                client.RbacV1Subject(
                    kind="ServiceAccount",
                    name="litmus-admin",
                    namespace=namespace,
                )
            ],
            role_ref=client.V1RoleRef(
                api_group="rbac.authorization.k8s.io",
                kind="ClusterRole",
                name=f"litmus-admin-{namespace}",
            ),
        )

        try:
            self.rbac_api.create_cluster_role_binding(cluster_role_binding)
        except ApiException as e:
            if e.status == 409:
                self.rbac_api.replace_cluster_role_binding(
                    f"litmus-admin-{namespace}-binding", cluster_role_binding
                )
            else:
                raise

        return True

    def install_experiment(self, experiment_type: str, namespace: str) -> bool:
        """Install a specific chaos experiment type.

        Args:
            experiment_type: The type of experiment (e.g., 'pod-delete').
            namespace: Target namespace.

        Returns:
            True if installation succeeded.
        """
        GITHUB_RAW_BASE = (
            "https://raw.githubusercontent.com/litmuschaos/chaos-charts/master/faults/kubernetes"
        )
        experiment_urls = {
            "pod-delete": f"{GITHUB_RAW_BASE}/pod-delete/fault.yaml",
            "container-kill": f"{GITHUB_RAW_BASE}/container-kill/fault.yaml",
            "pod-cpu-hog": f"{GITHUB_RAW_BASE}/pod-cpu-hog/fault.yaml",
            "pod-memory-hog": f"{GITHUB_RAW_BASE}/pod-memory-hog/fault.yaml",
            "pod-network-loss": f"{GITHUB_RAW_BASE}/pod-network-loss/fault.yaml",
            "pod-network-latency": f"{GITHUB_RAW_BASE}/pod-network-latency/fault.yaml",
            "pod-network-corruption": f"{GITHUB_RAW_BASE}/pod-network-corruption/fault.yaml",
            "pod-network-duplication": f"{GITHUB_RAW_BASE}/pod-network-duplication/fault.yaml",
            "pod-io-stress": f"{GITHUB_RAW_BASE}/pod-io-stress/fault.yaml",
            "node-drain": f"{GITHUB_RAW_BASE}/node-drain/fault.yaml",
            "node-cpu-hog": f"{GITHUB_RAW_BASE}/node-cpu-hog/fault.yaml",
            "node-memory-hog": f"{GITHUB_RAW_BASE}/node-memory-hog/fault.yaml",
            "node-taint": f"{GITHUB_RAW_BASE}/node-taint/fault.yaml",
        }

        url = experiment_urls.get(experiment_type)
        if not url:
            return False

        try:
            subprocess.run(
                ["kubectl", "apply", "-f", url, "-n", namespace],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    def _ensure_namespace(self, namespace: str):
        """Create namespace if it doesn't exist."""
        try:
            self.core_api.read_namespace(namespace)
        except ApiException as e:
            if e.status == 404:
                ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
                self.core_api.create_namespace(ns)
            else:
                raise

    def _wait_for_litmus(self, timeout: int) -> bool:
        """Wait for LitmusChaos to be ready."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.is_litmus_ready():
                return True
            time.sleep(5)

        return False

    def check_prerequisites(self) -> dict:
        """Check all prerequisites and return status.

        Returns:
            Dictionary with status of each prerequisite.
        """
        libvirt_status = self._check_libvirt()
        results = {
            "kubectl": self._check_kubectl(),
            "helm": self._check_helm(),
            "ansible": self._check_ansible(),
            "python_venv": self._check_python_venv(),
            "git": self._check_git(),
            "ssh": self._check_ssh(),
            "vagrant": self._check_vagrant(),
            "libvirt": libvirt_status["all_ready"],
            "libvirt_status": libvirt_status,
            "cluster_access": self._check_cluster_access(),
            "litmus_installed": self.is_litmus_installed() if self._k8s_initialized else False,
            "litmus_ready": self.is_litmus_ready() if self._k8s_initialized else False,
            "chaoscenter_installed": (
                self.is_chaoscenter_installed() if self._k8s_initialized else False
            ),
            "chaoscenter_ready": (
                self.is_chaoscenter_ready() if self._k8s_initialized else False
            ),
        }
        results["all_ready"] = all(
            [
                results["kubectl"],
                results["helm"],
                results["cluster_access"],
                results["litmus_installed"],
                results["litmus_ready"],
            ]
        )
        return results

    def _check_git(self) -> bool:
        """Check if git is available."""
        try:
            subprocess.run(
                ["git", "--version"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_ssh(self) -> bool:
        """Check if ssh is available."""
        try:
            subprocess.run(
                ["ssh", "-V"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_kubectl(self) -> bool:
        """Check if kubectl is available."""
        try:
            subprocess.run(
                ["kubectl", "version", "--client"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _check_helm(self) -> bool:
        """Check if helm is available."""
        try:
            subprocess.run(
                ["helm", "version"],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def install_helm(self) -> bool:
        """Install Helm automatically.

        Returns:
            True if installation succeeded.
        """
        system = platform.system().lower()
        machine = platform.machine().lower()

        if machine in ("x86_64", "amd64"):
            arch = "amd64"
        elif machine in ("aarch64", "arm64"):
            arch = "arm64"
        else:
            raise RuntimeError(f"Unsupported architecture: {machine}")

        if system == "linux":
            os_name = "linux"
        elif system == "darwin":
            os_name = "darwin"
        else:
            raise RuntimeError(f"Unsupported OS: {system}. Please install helm manually.")

        helm_version = "v3.14.0"
        filename = f"helm-{helm_version}-{os_name}-{arch}.tar.gz"
        url = f"https://get.helm.sh/{filename}"

        with tempfile.TemporaryDirectory() as tmpdir:
            tarball = Path(tmpdir) / filename
            extract_dir = Path(tmpdir) / "extract"
            extract_dir.mkdir()

            subprocess.run(
                ["curl", "-fsSL", "-o", str(tarball), url],
                check=True,
                capture_output=True,
            )

            subprocess.run(
                ["tar", "-xzf", str(tarball), "-C", str(extract_dir)],
                check=True,
                capture_output=True,
            )

            helm_binary = extract_dir / f"{os_name}-{arch}" / "helm"

            install_dir = Path.home() / ".local" / "bin"
            install_dir.mkdir(parents=True, exist_ok=True)
            dest = install_dir / "helm"

            shutil.copy2(helm_binary, dest)
            dest.chmod(0o755)

            if str(install_dir) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = f"{install_dir}:{os.environ.get('PATH', '')}"

        return self._check_helm()

    def ensure_helm(self) -> bool:
        """Ensure helm is installed, installing if necessary.

        Returns:
            True if helm is available.
        """
        if self._check_helm():
            return True

        self.install_helm()
        return self._check_helm()

    def _check_cluster_access(self) -> bool:
        """Check if we have cluster access."""
        if not self._k8s_initialized:
            return False
        try:
            self.core_api.list_namespace()
            return True
        except Exception:
            return False

    # -- metrics-server & Prometheus ----------------------------------------

    def is_metrics_server_installed(self) -> bool:
        """Check if metrics-server is available in the cluster."""
        if not self._k8s_initialized:
            return False
        try:
            custom = client.CustomObjectsApi()
            custom.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
            )
            return True
        except ApiException:
            return False

    def install_metrics_server(self, wait: bool = True, timeout: int = 120) -> bool:
        """Install metrics-server from the official manifest.

        Uses the high-availability manifest with ``--kubelet-insecure-tls``
        added for Vagrant/Kubespray clusters that use self-signed certs.

        Returns:
            True if installation succeeded.
        """
        manifest_url = (
            "https://github.com/kubernetes-sigs/metrics-server"
            "/releases/latest/download/components.yaml"
        )
        print("Installing metrics-server...")
        try:
            subprocess.run(
                ["kubectl", "apply", "-f", manifest_url],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to apply metrics-server manifest: {e}") from e

        # Patch to add --kubelet-insecure-tls for self-signed certs
        # (common in Vagrant/Kubespray clusters) and pin to control-plane
        # to avoid disruption from experiment placement strategies.
        patch = (
            '{"spec":{"template":{"spec":{'
            '"tolerations":[{"key":"node-role.kubernetes.io/control-plane",'
            '"operator":"Exists","effect":"NoSchedule"}],'
            '"nodeSelector":{"node-role.kubernetes.io/control-plane":""},'
            '"containers":[{'
            '"name":"metrics-server",'
            '"args":["--cert-dir=/tmp","--secure-port=10250",'
            '"--kubelet-preferred-address-types='
            'InternalIP,ExternalIP,Hostname",'
            '"--kubelet-use-node-status-port",'
            '"--metric-resolution=15s",'
            '"--kubelet-insecure-tls"]}]}}}}'
        )
        try:
            subprocess.run(
                [
                    "kubectl",
                    "patch",
                    "deployment",
                    "metrics-server",
                    "-n",
                    "kube-system",
                    "--type=strategic",
                    f"-p={patch}",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            # Patch may fail if args already set — non-fatal
            pass

        if wait:
            return self._wait_for_metrics_server(timeout)
        return True

    def _wait_for_metrics_server(self, timeout: int) -> bool:
        """Wait for metrics-server to become operational."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_metrics_server_installed():
                return True
            time.sleep(5)
        return False

    def is_prometheus_installed(self) -> bool:
        """Check if Prometheus is running in the cluster."""
        if not self._k8s_initialized:
            return False
        search_namespaces = ["monitoring", "prometheus", "kube-prometheus", "default"]
        search_names = ["prometheus-server", "prometheus", "prometheus-k8s"]
        for ns in search_namespaces:
            try:
                services = self.core_api.list_namespaced_service(ns)
                for svc in services.items:
                    if svc.metadata.name in search_names:
                        return True
            except ApiException:
                continue
        return False

    def install_prometheus(self, wait: bool = True, timeout: int = 180) -> bool:
        """Install Prometheus using the prometheus-community Helm chart.

        Installs a lightweight Prometheus server in the ``monitoring``
        namespace.  Alertmanager and pushgateway are disabled to keep
        resource usage low for thesis experiments.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace("monitoring")

        print("Adding prometheus-community Helm repository...")
        try:
            subprocess.run(
                [
                    "helm",
                    "repo",
                    "add",
                    "prometheus-community",
                    "https://prometheus-community.github.io/helm-charts",
                ],
                check=True,
            )
        except subprocess.CalledProcessError:
            pass  # Repo may already exist

        subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)

        print("Installing Prometheus...")
        try:
            subprocess.run(
                [
                    "helm",
                    "upgrade",
                    "--install",
                    "prometheus",
                    "prometheus-community/prometheus",
                    "--namespace",
                    "monitoring",
                    "--set",
                    "alertmanager.enabled=false",
                    "--set",
                    "kube-state-metrics.enabled=true",
                    "--set",
                    "prometheus-pushgateway.enabled=false",
                    "--set",
                    "server.persistentVolume.enabled=true",
                    "--set",
                    "server.persistentVolume.size=2Gi",
                    "--set",
                    "server.retention=3d",
                    "--set",
                    "server.global.scrape_interval=15s",
                    "--set",
                    "server.global.evaluation_interval=15s",
                    "--set",
                    "server.tolerations[0].key=node-role.kubernetes.io/control-plane",
                    "--set",
                    "server.tolerations[0].operator=Exists",
                    "--set",
                    "server.tolerations[0].effect=NoSchedule",
                    "--set",
                    "server.nodeSelector.node-role\\.kubernetes\\.io/control-plane=",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install Prometheus: {e}") from e

        if wait:
            return self._wait_for_prometheus(timeout)
        return True

    def _wait_for_prometheus(self, timeout: int) -> bool:
        """Wait for Prometheus server to become ready."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                pods = self.core_api.list_namespaced_pod(
                    "monitoring",
                    label_selector="app.kubernetes.io/name=prometheus",
                )
                for pod in pods.items:
                    if pod.status.phase == "Running":
                        ready = all(cs.ready for cs in (pod.status.container_statuses or []))
                        if ready:
                            return True
            except ApiException:
                pass
            time.sleep(5)
        return False

    # ------------------------------------------------------------------
    # Neo4j
    # ------------------------------------------------------------------

    def is_neo4j_installed(self) -> bool:
        """Check if Neo4j is running in the cluster."""
        if not self._k8s_initialized:
            return False
        for ns in ("neo4j", "default", "monitoring"):
            try:
                services = self.core_api.list_namespaced_service(ns)
                for svc in services.items:
                    if svc.metadata.name in ("neo4j", "neo4j-lb"):
                        return True
            except ApiException:
                continue
        return False

    def _ensure_storage_class(self) -> None:
        """Install local-path-provisioner if no StorageClass exists."""
        try:
            sc_list = self.storage_api.list_storage_class()
            if sc_list.items:
                return
        except Exception:
            pass

        print("No StorageClass found. Installing local-path-provisioner...")
        subprocess.run(
            [
                "kubectl",
                "apply",
                "-f",
                "https://raw.githubusercontent.com/rancher/local-path-provisioner/"
                "v0.0.26/deploy/local-path-storage.yaml",
            ],
            check=True,
        )
        # Mark as default StorageClass
        subprocess.run(
            [
                "kubectl",
                "patch",
                "storageclass",
                "local-path",
                "-p",
                '{"metadata":{"annotations":'
                '{"storageclass.kubernetes.io/is-default-class":"true"}}}',
            ],
            check=True,
        )
        print("  local-path-provisioner installed")

    def install_neo4j(self, wait: bool = True, timeout: int = 300) -> bool:
        """Install Neo4j as a lightweight Deployment.

        Deploys a Neo4j Community instance in the ``neo4j`` namespace
        using a plain Deployment + Service (no Helm chart) to keep
        resource usage low for thesis clusters with limited memory.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace("neo4j")
        self._ensure_storage_class()

        pvc_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "neo4j-data", "namespace": "neo4j"},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Gi"}},
            },
        }

        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "neo4j", "namespace": "neo4j"},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "neo4j"}},
                "template": {
                    "metadata": {"labels": {"app": "neo4j"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "neo4j",
                                "image": "neo4j:5-community",
                                "env": [
                                    {"name": "NEO4J_AUTH", "value": "neo4j/chaosprobe"},
                                    {
                                        "name": "NEO4J_server_memory_heap_initial__size",
                                        "value": "256m",
                                    },
                                    {"name": "NEO4J_server_memory_heap_max__size", "value": "256m"},
                                    {"name": "NEO4J_server_memory_pagecache_size", "value": "64m"},
                                    {
                                        "name": "NEO4J_server_config_strict__validation_enabled",
                                        "value": "false",
                                    },
                                ],
                                "ports": [
                                    {"containerPort": 7474, "name": "http"},
                                    {"containerPort": 7687, "name": "bolt"},
                                ],
                                "resources": {
                                    "requests": {"cpu": "250m", "memory": "512Mi"},
                                    "limits": {"cpu": "500m", "memory": "768Mi"},
                                },
                                "readinessProbe": {
                                    "tcpSocket": {"port": 7687},
                                    "initialDelaySeconds": 30,
                                    "periodSeconds": 5,
                                    "failureThreshold": 12,
                                },
                                "volumeMounts": [
                                    {
                                        "name": "neo4j-data",
                                        "mountPath": "/data",
                                    }
                                ],
                            }
                        ],
                        "volumes": [
                            {
                                "name": "neo4j-data",
                                "persistentVolumeClaim": {"claimName": "neo4j-data"},
                            }
                        ],
                    },
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "neo4j", "namespace": "neo4j"},
            "spec": {
                "selector": {"app": "neo4j"},
                "ports": [
                    {"name": "http", "port": 7474, "targetPort": 7474},
                    {"name": "bolt", "port": 7687, "targetPort": 7687},
                ],
            },
        }

        print("Installing Neo4j...")
        try:
            from kubernetes.utils import create_from_dict

            k8s_client = client.ApiClient()

            # Apply PVC (skip if already exists)
            try:
                self.core_api.read_namespaced_persistent_volume_claim("neo4j-data", "neo4j")
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, pvc_manifest)
                else:
                    raise

            # Apply deployment
            try:
                self.apps_api.read_namespaced_deployment("neo4j", "neo4j")
                self.apps_api.patch_namespaced_deployment("neo4j", "neo4j", manifest)
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, manifest)
                else:
                    raise

            # Apply service
            try:
                self.core_api.read_namespaced_service("neo4j", "neo4j")
            except ApiException as e:
                if e.status == 404:
                    create_from_dict(k8s_client, svc_manifest)
                else:
                    raise
        except Exception as e:
            raise RuntimeError(f"Failed to install Neo4j: {e}") from e

        if wait:
            return self._wait_for_neo4j(timeout)
        return True

    def _wait_for_neo4j(self, timeout: int) -> bool:
        """Wait for Neo4j to become ready."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                pods = self.core_api.list_namespaced_pod(
                    "neo4j",
                    label_selector="app=neo4j",
                )
                for pod in pods.items:
                    if pod.status.phase == "Running":
                        ready = all(cs.ready for cs in (pod.status.container_statuses or []))
                        if ready:
                            return True
            except ApiException:
                pass
            time.sleep(5)
        return False

    # -------------------------------------------------------------------------
    # ChaosCenter (Dashboard) management
    # -------------------------------------------------------------------------

    def is_chaoscenter_installed(self) -> bool:
        """Check if ChaosCenter dashboard is installed."""
        if not self._k8s_initialized:
            return False
        try:
            svcs = self.core_api.list_namespaced_service(self.LITMUS_NAMESPACE)
            svc_names = {s.metadata.name for s in svcs.items}
            return self.CHAOSCENTER_FRONTEND_SVC in svc_names
        except Exception:
            return False

    def is_chaoscenter_ready(self) -> bool:
        """Check if ChaosCenter pods are running and ready."""
        if not self.is_chaoscenter_installed():
            return False
        try:
            deployments = self.apps_api.list_namespaced_deployment(self.LITMUS_NAMESPACE)
            required_fragments = ["frontend", "server", "auth"]
            for frag in required_fragments:
                found_ready = False
                for dep in deployments.items:
                    if frag in dep.metadata.name.lower():
                        if (
                            dep.status.ready_replicas is not None
                            and dep.status.ready_replicas == dep.spec.replicas
                        ):
                            found_ready = True
                            break
                if not found_ready:
                    return False
            return True
        except Exception:
            return False

    def install_chaoscenter(
        self,
        service_type: str = "NodePort",
        wait: bool = True,
        timeout: int = 300,
    ) -> bool:
        """Install ChaosCenter (full dashboard) using Helm.

        This installs the full ``litmuschaos/litmus`` chart which includes:
        frontend, GraphQL server, auth-server, MongoDB, subscriber,
        chaos-operator, chaos-exporter, and workflow-controller.

        Args:
            service_type: Kubernetes service type for frontend (NodePort or LoadBalancer).
            wait: Whether to wait for all pods to become ready.
            timeout: Timeout in seconds.

        Returns:
            True if installation succeeded.
        """
        self._ensure_namespace(self.LITMUS_NAMESPACE)

        # Ensure the helm repo is present
        try:
            subprocess.run(
                [
                    "helm", "repo", "add", "litmuschaos",
                    "https://litmuschaos.github.io/litmus-helm/",
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # repo may already exist

        try:
            subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to update helm repos: {e}") from e

        print("Installing ChaosCenter dashboard...")
        try:
            subprocess.run(
                [
                    "helm", "upgrade", "--install",
                    self.CHAOSCENTER_RELEASE_NAME,
                    self.CHAOSCENTER_HELM_CHART,
                    "--namespace", self.LITMUS_NAMESPACE,
                    "--set", f"portal.frontend.service.type={service_type}",
                    "--set", f"portal.server.service.type={service_type}",
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install ChaosCenter: {e}") from e

        if wait:
            return self._wait_for_chaoscenter(timeout)
        return True

    def _wait_for_chaoscenter(self, timeout: int) -> bool:
        """Wait for ChaosCenter to become ready."""
        start = time.time()
        last_status = ""
        while time.time() - start < timeout:
            if self.is_chaoscenter_ready():
                print("  ChaosCenter: all pods ready")
                return True
            # Print progress so the user doesn't think we're stuck
            try:
                pods = self.core_api.list_namespaced_pod(self.LITMUS_NAMESPACE)
                statuses = []
                for pod in pods.items:
                    name = pod.metadata.name
                    phase = pod.status.phase or "Unknown"
                    cs = pod.status.container_statuses or []
                    if cs and all(c.ready for c in cs):
                        statuses.append(f"{name}=Ready")
                    else:
                        # Show init container status if stuck there
                        init_cs = pod.status.init_container_statuses or []
                        if init_cs and not all(c.ready for c in init_cs):
                            statuses.append(f"{name}=Init")
                        else:
                            statuses.append(f"{name}={phase}")
                status_line = ", ".join(sorted(statuses))
                elapsed = int(time.time() - start)
                msg = f"  Waiting for ChaosCenter pods ({elapsed}s): {status_line}"
                if msg != last_status:
                    print(msg)
                    last_status = msg
            except Exception:
                pass
            time.sleep(10)
        return False

    def get_chaoscenter_status(self) -> dict:
        """Return detailed status of the ChaosCenter deployment.

        Returns:
            Dictionary with keys: installed, ready, pods, frontend_url.
        """
        result: dict[str, Any] = {
            "installed": self.is_chaoscenter_installed(),
            "ready": False,
            "pods": [],
            "frontend_url": None,
        }
        if not result["installed"]:
            return result

        result["ready"] = self.is_chaoscenter_ready()

        try:
            pods = self.core_api.list_namespaced_pod(self.LITMUS_NAMESPACE)
            for pod in pods.items:
                containers = pod.status.container_statuses or []
                result["pods"].append(
                    {
                        "name": pod.metadata.name,
                        "phase": pod.status.phase,
                        "ready": all(c.ready for c in containers),
                    }
                )
        except Exception:
            pass

        result["frontend_url"] = self.get_dashboard_url()
        return result

    def get_dashboard_url(self) -> Optional[str]:
        """Detect and return the ChaosCenter frontend URL.

        Supports NodePort services (returns ``http://<node>:<nodePort>``)
        and LoadBalancer services.  Returns ``None`` when the URL cannot
        be determined.
        """
        if not self._k8s_initialized:
            return None
        try:
            svc = self.core_api.read_namespaced_service(
                self.CHAOSCENTER_FRONTEND_SVC, self.LITMUS_NAMESPACE,
            )
        except Exception:
            return None

        svc_type = svc.spec.type
        port_obj = svc.spec.ports[0] if svc.spec.ports else None
        if port_obj is None:
            return None

        if svc_type == "LoadBalancer":
            ingress = (svc.status.load_balancer or {}).ingress
            if ingress:
                host = ingress[0].ip or ingress[0].hostname
                return f"http://{host}:{port_obj.port}"
            return None

        if svc_type == "NodePort" and port_obj.node_port:
            node_ip = self._get_node_ip()
            if node_ip:
                return f"http://{node_ip}:{port_obj.node_port}"
            return None

        return None

    def _get_node_ip(self) -> Optional[str]:
        """Return the IP of the first schedulable node."""
        try:
            nodes = self.core_api.list_node()
            for node in nodes.items:
                for addr in node.status.addresses:
                    if addr.type == "InternalIP":
                        return addr.address
        except Exception:
            pass
        return None

    def _chaoscenter_api_request(
        self,
        url: str,
        method: str = "POST",
        data: Optional[dict] = None,
        token: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the ChaosCenter API.

        Args:
            url: Full URL including endpoint path.
            method: HTTP method.
            data: JSON-serialisable body (for POST/PUT).
            token: Bearer token for authenticated requests.
            headers: Additional HTTP headers.

        Returns:
            Parsed JSON response as a dict.
        """
        body = _json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = _json.loads(resp.read().decode())
            # Surface GraphQL-level errors that arrive with HTTP 200
            if (
                isinstance(result, dict)
                and result.get("errors")
                and result.get("data") is None
            ):
                errors = result["errors"]
                msg = (
                    errors[0].get("message", str(errors))
                    if errors
                    else str(result)
                )
                raise RuntimeError(f"ChaosCenter GraphQL error: {msg}")
            return result
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            raise RuntimeError(
                f"ChaosCenter API error {e.code}: {body_text}"
            ) from e

    def _chaoscenter_authenticate(
        self, server_url: str, username: str, password: str,
    ) -> dict:
        """Authenticate against ChaosCenter and return login response.

        Args:
            server_url: Base URL of the auth server (e.g. ``http://host:port``).
            username: ChaosCenter username.
            password: ChaosCenter password.

        Returns:
            Dict with ``accessToken``, ``projectID``, and other keys.
        """
        resp = self._chaoscenter_api_request(
            f"{server_url}/login",
            data={"username": username, "password": password},
        )
        token = (
            resp.get("accessToken")
            or resp.get("access_token")
            or resp.get("token")
        )
        if not token:
            raise RuntimeError("Failed to obtain ChaosCenter access token")
        return resp

    CHAOSCENTER_AUTH_PORT = 9003
    CHAOSCENTER_MANAGED_PASS = "ChaosProbe1!"

    def _chaoscenter_change_password(
        self, auth_url: str, username: str, old_password: str, new_password: str,
        token: str = "",
    ) -> None:
        """Change ChaosCenter password via the auth API."""
        self._chaoscenter_api_request(
            f"{auth_url}/update/password",
            data={
                "username": username,
                "oldPassword": old_password,
                "newPassword": new_password,
            },
            token=token,
        )

    def _chaoscenter_gql_url(self, base_host: str) -> str:
        """Return the GraphQL endpoint URL for a given host."""
        return f"{base_host}:{self.CHAOSCENTER_SERVER_PORT}/query"

    def _chaoscenter_auth_url(self, base_host: str) -> str:
        """Return the auth server base URL for a given host."""
        return f"{base_host}:{self.CHAOSCENTER_AUTH_PORT}"

    def _chaoscenter_login(
        self,
        auth_url: str,
        username: str = "",
        password: str = "",
    ) -> tuple[str, str]:
        """Authenticate and return (token, project_id).

        Tries the provided password first, then the managed password,
        then the factory default.  If the factory default works the
        password is automatically rotated to the managed password.
        """
        username = username or self.CHAOSCENTER_DEFAULT_USER
        candidates = []
        if password:
            candidates.append(password)
        if self.CHAOSCENTER_MANAGED_PASS not in candidates:
            candidates.append(self.CHAOSCENTER_MANAGED_PASS)
        if self.CHAOSCENTER_DEFAULT_PASS not in candidates:
            candidates.append(self.CHAOSCENTER_DEFAULT_PASS)

        last_err: Optional[Exception] = None
        for pwd in candidates:
            try:
                resp = self._chaoscenter_authenticate(auth_url, username, pwd)
                token = (
                    resp.get("accessToken")
                    or resp.get("access_token")
                    or resp.get("token")
                )
                project_id = resp.get("projectID", "")

                # Auto-rotate factory default → managed password
                if pwd == self.CHAOSCENTER_DEFAULT_PASS and pwd != self.CHAOSCENTER_MANAGED_PASS:
                    try:
                        self._chaoscenter_change_password(
                            auth_url, username,
                            self.CHAOSCENTER_DEFAULT_PASS,
                            self.CHAOSCENTER_MANAGED_PASS,
                            token=token,
                        )
                        # Re-login with the new password
                        resp2 = self._chaoscenter_authenticate(
                            auth_url, username, self.CHAOSCENTER_MANAGED_PASS,
                        )
                        token = (
                            resp2.get("accessToken")
                            or resp2.get("access_token")
                            or resp2.get("token")
                        )
                        project_id = resp2.get("projectID", project_id)
                        print(
                            "  ChaosCenter: default password rotated to managed password"
                        )
                    except Exception:
                        pass  # keep using the default-password token

                return token, project_id
            except Exception as exc:
                last_err = exc

        raise RuntimeError(
            f"ChaosCenter authentication failed (tried {len(candidates)} passwords): {last_err}"
        )

    def _chaoscenter_list_environments(
        self, gql_url: str, project_id: str, token: str,
    ) -> list[dict]:
        """Return existing environments for the given project."""
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!) { listEnvironments(projectID: $pid) "
                    "{ environments { environmentID name } } }"
                ),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("listEnvironments", {})
            .get("environments")
        ) or []

    def _chaoscenter_list_infras(
        self, gql_url: str, project_id: str, token: str,
    ) -> list[dict]:
        """Return registered infrastructures for the given project."""
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!) { listInfras(projectID: $pid) "
                    "{ infras { infraID name environmentID isActive "
                    "isInfraConfirmed infraNamespace } } }"
                ),
                "variables": {"pid": project_id},
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("listInfras", {})
            .get("infras")
        ) or []

    def _chaoscenter_create_environment(
        self, gql_url: str, project_id: str, env_name: str, token: str,
    ) -> str:
        """Create a ChaosCenter environment and return its ID."""
        env_query = (
            "mutation($pid: ID!, $req: CreateEnvironmentRequest!) "
            "{ createEnvironment(projectID: $pid, request: $req) "
            "{ environmentID } }"
        )
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": env_query,
                "variables": {
                    "pid": project_id,
                    "req": {
                        "name": env_name,
                        "environmentID": env_name,
                        "type": "NON_PROD",
                    },
                },
            },
            token=token,
        )
        return (
            resp.get("data", {})
            .get("createEnvironment", {})
            .get("environmentID", env_name)
        )

    def _chaoscenter_server_internal_url(self) -> str:
        """Return the cluster-internal URL of the ChaosCenter frontend.

        The ChaosCenter server derives ``SERVER_ADDR`` by appending
        ``/api/query`` to the ``Referer`` header.  Inside the cluster the
        subscriber must reach the server through the **frontend** service
        (which proxies ``/api/`` to the GraphQL server), so we use the
        frontend service DNS name here.
        """
        return (
            f"http://{self.CHAOSCENTER_FRONTEND_SVC}"
            f".{self.LITMUS_NAMESPACE}.svc.cluster.local"
            f":{self.CHAOSCENTER_FRONTEND_PORT}"
        )

    def _chaoscenter_register_infra(
        self,
        gql_url: str,
        project_id: str,
        env_id: str,
        namespace: str,
        token: str,
    ) -> dict:
        """Register namespace as infrastructure and return {infraID, manifest}."""
        infra_query = (
            "mutation($pid: ID!, $req: RegisterInfraRequest!) "
            "{ registerInfra(projectID: $pid, request: $req) "
            "{ infraID manifest token } }"
        )
        # The server reads the Referer header to build the SERVER_ADDR
        # that the subscriber uses *inside the cluster*.  Must be the
        # cluster-internal service URL, not a localhost port-forward.
        referer = self._chaoscenter_server_internal_url()
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": infra_query,
                "variables": {
                    "pid": project_id,
                    "req": {
                        "name": f"chaosprobe-{namespace}",
                        "environmentID": env_id,
                        "description": f"ChaosProbe infra for {namespace}",
                        "infraNamespace": namespace,
                        "infraScope": "namespace",
                        "infrastructureType": "Kubernetes",
                        "platformName": "kubernetes",
                        "infraNsExists": True,
                        "skipSsl": True,
                    },
                },
            },
            token=token,
            headers={"Referer": referer},
        )
        result = resp.get("data", {}).get("registerInfra", {})
        if not result.get("infraID"):
            raise RuntimeError("Failed to register infrastructure in ChaosCenter")
        return result

    def _apply_manifest(self, manifest: str, namespace: str) -> None:
        """Write *manifest* to a temp file and ``kubectl apply`` it."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False,
        ) as f:
            f.write(manifest)
            f.flush()
            try:
                subprocess.run(
                    ["kubectl", "apply", "-f", f.name, "-n", namespace],
                    check=True,
                    capture_output=True,
                )
            finally:
                os.unlink(f.name)

    def ensure_chaoscenter_configured(
        self,
        namespace: str,
        base_host: str = "http://localhost",
        username: str = "",
        password: str = "",
        timeout: int = 120,
    ) -> dict:
        """Idempotently configure ChaosCenter for *namespace*.

        1. Authenticate (auto-rotates default password).
        2. Create environment ``chaosprobe-<ns>`` if absent.
        3. Register infrastructure + apply subscriber if absent.
        4. Wait for subscriber pod to appear.

        Args:
            namespace: Target Kubernetes namespace.
            base_host: Scheme + host (no port), e.g. ``http://localhost``.
            username: ChaosCenter username.
            password: ChaosCenter password (optional — tries managed/default).
            timeout: Seconds to wait for subscriber readiness.

        Returns:
            Dict with ``token``, ``project_id``, ``environment_id``,
            ``infra_id`` keys.
        """
        auth_url = self._chaoscenter_auth_url(base_host)
        gql_url = self._chaoscenter_gql_url(base_host)

        # --- authenticate ------------------------------------------------
        token, project_id = self._chaoscenter_login(
            auth_url, username=username, password=password,
        )
        if not project_id:
            raise RuntimeError("ChaosCenter login did not return a projectID")

        env_name = f"chaosprobe-{namespace}"

        # --- environment -------------------------------------------------
        envs = self._chaoscenter_list_environments(gql_url, project_id, token)
        env_ids = {e["environmentID"] for e in envs}
        if env_name not in env_ids:
            self._chaoscenter_create_environment(gql_url, project_id, env_name, token)
            print(f"  ChaosCenter: created environment '{env_name}'")
        else:
            print(f"  ChaosCenter: environment '{env_name}' exists")

        # --- infrastructure ----------------------------------------------
        infras = self._chaoscenter_list_infras(gql_url, project_id, token)
        existing = [
            i for i in infras
            if i.get("infraNamespace") == namespace
            and i.get("environmentID") == env_name
        ]

        if existing and existing[0].get("isActive"):
            infra_id = existing[0]["infraID"]
            print(f"  ChaosCenter: infrastructure already active ({infra_id})")
        elif existing:
            # Infra registered but subscriber not yet connected — don't
            # re-register (which would create a duplicate).  Just ensure
            # the subscriber deployment exists and wait for it.
            infra_id = existing[0]["infraID"]
            confirmed = existing[0].get("isInfraConfirmed", False)
            print(
                f"  ChaosCenter: infrastructure registered, "
                f"awaiting subscriber connection ({infra_id})"
            )
            if not confirmed:
                # Subscriber may have been evicted — check if deployment exists
                try:
                    self.apps_api.read_namespaced_deployment(
                        "subscriber", namespace,
                    )
                except ApiException as exc:
                    if exc.status == 404:
                        # Deployment gone — re-apply the manifest by
                        # fetching it from the server
                        print("  ChaosCenter: subscriber deployment missing — re-applying")
                        try:
                            manifest_resp = self._chaoscenter_api_request(
                                gql_url,
                                data={
                                    "query": (
                                        "query($pid: ID!, $iid: String!) "
                                        "{ getInfraManifest(projectID: $pid, "
                                        "infraID: $iid) }"
                                    ),
                                    "variables": {
                                        "pid": project_id,
                                        "iid": infra_id,
                                    },
                                },
                                token=token,
                                headers={
                                    "Referer": self._chaoscenter_server_internal_url(),
                                },
                            )
                            manifest = (
                                manifest_resp.get("data", {})
                                .get("getInfraManifest", "")
                            )
                            if manifest:
                                self._apply_manifest(manifest, namespace)
                        except Exception as e:
                            print(f"  ChaosCenter: WARNING - could not re-apply manifest: {e}")

            # Wait for subscriber pod readiness
            start = time.time()
            while time.time() - start < timeout:
                try:
                    pods = self.core_api.list_namespaced_pod(
                        namespace,
                        label_selector="app=subscriber",
                    )
                    if pods.items and all(
                        c.ready
                        for p in pods.items
                        for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                print(
                    "  ChaosCenter: WARNING - subscriber pod not ready "
                    f"after {timeout}s (check cluster resources)",
                )
        else:
            # No infra exists — register a new one
            result = self._chaoscenter_register_infra(
                gql_url, project_id, env_name, namespace, token,
            )
            infra_id = result["infraID"]
            manifest = result.get("manifest", "")
            if manifest:
                self._apply_manifest(manifest, namespace)
                print(f"  ChaosCenter: subscriber manifest applied to '{namespace}'")

            # Wait for subscriber pod
            start = time.time()
            while time.time() - start < timeout:
                try:
                    pods = self.core_api.list_namespaced_pod(
                        namespace,
                        label_selector="app=subscriber",
                    )
                    if pods.items and all(
                        c.ready
                        for p in pods.items
                        for c in (p.status.container_statuses or [])
                    ):
                        print("  ChaosCenter: subscriber pod ready")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                print(
                    "  ChaosCenter: WARNING - subscriber pod not ready "
                    f"after {timeout}s",
                )

        return {
            "token": token,
            "project_id": project_id,
            "environment_id": env_name,
            "infra_id": infra_id,
        }

    def connect_infrastructure(
        self,
        namespace: str,
        dashboard_url: Optional[str] = None,
        username: str = "",
        password: str = "",
    ) -> dict:
        """Register a namespace as chaos infrastructure in ChaosCenter.

        This authenticates to ChaosCenter, creates a new environment
        (if needed), and registers the namespace as a Kubernetes
        infrastructure via the GraphQL API.

        Args:
            namespace: The Kubernetes namespace to register.
            dashboard_url: Override auto-detected dashboard URL.
            username: ChaosCenter username (defaults to ``admin``).
            password: ChaosCenter password (defaults to ``litmus``).

        Returns:
            Dict with ``infra_id`` and ``manifest`` keys on success.
        """
        base_url = dashboard_url or self.get_dashboard_url()
        if not base_url:
            raise RuntimeError(
                "Cannot detect ChaosCenter URL. Is it installed and ready?"
            )

        # Derive scheme + host (strip port)
        base_host = base_url.rsplit(":", 1)[0]

        result = self.ensure_chaoscenter_configured(
            namespace=namespace,
            base_host=base_host,
            username=username,
            password=password,
        )
        return {"infra_id": result["infra_id"], "manifest": ""}

    def chaoscenter_save_experiment(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        infra_id: str,
        experiment_id: str,
        name: str,
        manifest: str,
        description: str = "",
    ) -> str:
        """Save a chaos experiment in ChaosCenter.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            infra_id: Registered infrastructure ID.
            experiment_id: Unique experiment ID.
            name: Human-readable experiment name.
            manifest: Argo Workflow manifest YAML string.
            description: Optional description.

        Returns:
            The experiment ID as confirmed by the server.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($pid: ID!, $req: SaveChaosExperimentRequest!) "
                    "{ saveChaosExperiment(projectID: $pid, request: $req) }"
                ),
                "variables": {
                    "pid": project_id,
                    "req": {
                        "id": experiment_id,
                        "type": "Experiment",
                        "name": name,
                        "description": description or f"ChaosProbe experiment: {name}",
                        "manifest": manifest,
                        "infraID": infra_id,
                        "tags": ["chaosprobe"],
                    },
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("saveChaosExperiment", experiment_id)

    def chaoscenter_run_experiment(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        experiment_id: str,
    ) -> str:
        """Trigger execution of a saved chaos experiment.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            experiment_id: ID of the experiment to run.

        Returns:
            The notifyID for tracking the experiment run.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "mutation($eid: String!, $pid: ID!) "
                    "{ runChaosExperiment(experimentID: $eid, projectID: $pid) "
                    "{ notifyID } }"
                ),
                "variables": {
                    "eid": experiment_id,
                    "pid": project_id,
                },
            },
            token=token,
        )
        return (
            (resp.get("data") or {})
            .get("runChaosExperiment", {})
            .get("notifyID", "")
        )

    def chaoscenter_get_experiment_run(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        notify_id: str,
    ) -> dict[str, Any]:
        """Query the status of a running experiment.

        Args:
            gql_url: GraphQL endpoint URL.
            project_id: ChaosCenter project ID.
            token: Bearer token.
            notify_id: The notifyID returned by ``runChaosExperiment``.

        Returns:
            Dict with at least ``phase`` key (e.g. ``Running``,
            ``Completed``, ``Error``).  Also includes
            ``resiliencyScore``, ``faultsPassed``, ``faultsFailed``,
            ``totalFaults`` when available.
        """
        resp = self._chaoscenter_api_request(
            gql_url,
            data={
                "query": (
                    "query($pid: ID!, $nid: ID) "
                    "{ getExperimentRun(projectID: $pid, notifyID: $nid) "
                    "{ experimentRunID phase resiliencyScore "
                    "faultsPassed faultsFailed faultsAwaited "
                    "faultsStopped totalFaults } }"
                ),
                "variables": {
                    "pid": project_id,
                    "nid": notify_id,
                },
            },
            token=token,
        )
        return (resp.get("data") or {}).get("getExperimentRun", {})
