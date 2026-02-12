"""Automatic setup and installation of LitmusChaos and dependencies."""

import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

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

    VAGRANTFILE_TEMPLATE = '''# -*- mode: ruby -*-
# vi: set ft=ruby :

# ChaosProbe Vagrant Configuration
# Auto-generated - do not edit directly

CLUSTER_NAME = "{cluster_name}"
NUM_CONTROL_PLANES = {num_control_planes}
NUM_WORKERS = {num_workers}
VM_MEMORY = {vm_memory}
VM_CPUS = {vm_cpus}
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
        vb.memory = VM_MEMORY
        vb.cpus = VM_CPUS
        vb.customize ["modifyvm", :id, "--natdnshostresolver1", "on"]
      end

      node.vm.provider "libvirt" do |lv|
        lv.memory = VM_MEMORY
        lv.cpus = VM_CPUS
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
        vb.memory = VM_MEMORY
        vb.cpus = VM_CPUS
        vb.customize ["modifyvm", :id, "--natdnshostresolver1", "on"]
      end

      node.vm.provider "libvirt" do |lv|
        lv.memory = VM_MEMORY
        lv.cpus = VM_CPUS
      end

      # Enable password-less sudo
      node.vm.provision "shell", inline: <<-SHELL
        echo "vagrant ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vagrant
        chmod 440 /etc/sudoers.d/vagrant
      SHELL
    end
  end
end
'''

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
                ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                server = result.stdout.strip()
                info["server"] = server
                # Check if it's a local cluster
                local_indicators = ["localhost", "127.0.0.1", "0.0.0.0", "minikube", "kind", "k3s", "k3d", "docker-desktop"]
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

        # Try to actually access the cluster
        if self._k8s_initialized:
            try:
                self.core_api.list_namespace()
                return True, f"Connected to: {info['context']} ({info['server']})"
            except ApiException as e:
                return False, f"Cluster access error: {e.reason}"

        return True, f"Connected to: {info['context']} ({info['server']})"

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
                    "git", "clone", "--depth", "1",
                    "--branch", self.KUBESPRAY_VERSION,
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
                }
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
            "-i", str(inventory_dir / "hosts.yaml"),
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
            raise RuntimeError(f"Cluster deployment failed: {e}")

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
            raise RuntimeError(f"Failed to fetch kubeconfig: {e}")

        # Update the server address in kubeconfig to use external IP
        with open(output_path, "r") as f:
            kubeconfig_content = f.read()

        # Replace internal IP with control plane host IP
        kubeconfig_content = kubeconfig_content.replace(
            "server: https://127.0.0.1:",
            f"server: https://{control_plane_host}:"
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
            "-i", str(inventory_dir / "hosts.yaml"),
            str(kubespray_dir / "reset.yml"),
            "-b",
            "-e", "reset_confirmation=yes",
        ]

        if become_pass:
            cmd.extend(["--become-password", become_pass])

        print("Destroying Kubernetes cluster...")
        try:
            subprocess.run(cmd, check=True, cwd=str(kubespray_dir))
            print("Cluster destroyed!")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Cluster destruction failed: {e}")

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

        result["all_ready"] = all([
            result["kvm_available"],
            result["libvirtd_installed"],
            result["libvirtd_running"],
            result["user_in_groups"],
            result["vagrant_libvirt_plugin"],
        ])

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
            raise RuntimeError(f"Failed to install packages: {e}")

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
            raise RuntimeError(f"Failed to add user to groups: {e}")

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
            raise RuntimeError(f"Failed to install vagrant-libvirt plugin: {e}")

        return result

    def ensure_libvirt(self) -> dict:
        """Ensure libvirt is installed, installing if necessary.

        Returns:
            Dictionary with libvirt status.
        """
        status = self._check_libvirt()

        if status["all_ready"]:
            return status

        # Install if not ready
        install_result = self.install_libvirt()

        # Re-check status
        status = self._check_libvirt()
        status["install_result"] = install_result

        return status

    def create_vagrantfile(
        self,
        cluster_name: str = "chaosprobe",
        num_control_planes: int = 1,
        num_workers: int = 2,
        vm_memory: int = 2048,
        vm_cpus: int = 2,
        box_image: str = "generic/ubuntu2204",
        network_prefix: str = "192.168.56",
        output_dir: Optional[Path] = None,
    ) -> Path:
        """Create a Vagrantfile for local cluster VMs.

        Args:
            cluster_name: Name for the cluster.
            num_control_planes: Number of control plane nodes.
            num_workers: Number of worker nodes.
            vm_memory: Memory per VM in MB.
            vm_cpus: CPUs per VM.
            box_image: Vagrant box image to use.
            network_prefix: Network prefix for private IPs (e.g., 192.168.56).
            output_dir: Directory to create Vagrantfile in.

        Returns:
            Path to the created Vagrantfile directory.
        """
        if output_dir is None:
            output_dir = self.VAGRANT_DIR / cluster_name
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        vagrantfile_content = self.VAGRANTFILE_TEMPLATE.format(
            cluster_name=cluster_name,
            num_control_planes=num_control_planes,
            num_workers=num_workers,
            vm_memory=vm_memory,
            vm_cpus=vm_cpus,
            box_image=box_image,
            network_prefix=network_prefix,
        )

        vagrantfile_path = output_dir / "Vagrantfile"
        with open(vagrantfile_path, "w") as f:
            f.write(vagrantfile_content)

        print(f"Created Vagrantfile at: {output_dir}")
        return output_dir

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
            raise RuntimeError(f"Failed to start Vagrant VMs: {e}")

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
            raise RuntimeError(f"Failed to destroy Vagrant VMs: {e}")

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
            raise RuntimeError(f"Failed to get Vagrant status: {e}")

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
                    inventory["all"]["hosts"][host["name"]]["ansible_ssh_private_key_file"] = host["ansible_ssh_private_key_file"]
                inventory["all"]["hosts"][host["name"]]["ansible_ssh_common_args"] = "-o StrictHostKeyChecking=no"

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
                crd for crd in crds.items
                if crd.metadata.name.endswith(".litmuschaos.io")
            ]
            return len(litmus_crds) > 0
        except (ApiException, AttributeError):
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
        except ApiException:
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
                ["helm", "repo", "add", "litmuschaos",
                 "https://litmuschaos.github.io/litmus-helm/"],
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
            raise RuntimeError(f"Failed to update helm repos: {e}")

        # Install litmus-core chart (chaos operator and CRDs)
        print("Installing LitmusChaos operator...")
        try:
            subprocess.run(
                [
                    "helm", "upgrade", "--install", "litmus",
                    "litmuschaos/litmus-core",
                    "--namespace", self.LITMUS_NAMESPACE,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install LitmusChaos operator: {e}")

        # Install kubernetes-chaos chart (experiment definitions)
        print("Installing LitmusChaos experiments...")
        try:
            subprocess.run(
                [
                    "helm", "upgrade", "--install", "chaos-experiments",
                    "litmuschaos/kubernetes-chaos",
                    "--namespace", self.LITMUS_NAMESPACE,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to install LitmusChaos experiments: {e}")

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
                    resources=["pods", "pods/log", "pods/exec", "events", "services",
                              "configmaps", "secrets", "persistentvolumeclaims", "nodes"],
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
                self.rbac_api.replace_cluster_role(
                    f"litmus-admin-{namespace}", cluster_role
                )
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
        GITHUB_RAW_BASE = "https://raw.githubusercontent.com/litmuschaos/chaos-charts/master/faults/kubernetes"
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

    def full_setup(self, namespace: str, experiments: Optional[list] = None) -> bool:
        """Perform full setup: install Litmus, RBAC, and experiments.

        Args:
            namespace: Target namespace for chaos experiments.
            experiments: List of experiment types to install.

        Returns:
            True if all setup succeeded.
        """
        if not self.is_litmus_installed():
            self.install_litmus(wait=True)

        self.setup_rbac(namespace)

        if experiments:
            for exp_type in experiments:
                self.install_experiment(exp_type, namespace)

        return True

    def _ensure_namespace(self, namespace: str):
        """Create namespace if it doesn't exist."""
        try:
            self.core_api.read_namespace(namespace)
        except ApiException as e:
            if e.status == 404:
                ns = client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace)
                )
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
        }
        results["all_ready"] = all([
            results["kubectl"],
            results["helm"],
            results["cluster_access"],
            results["litmus_installed"],
            results["litmus_ready"],
        ])
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
        except (ApiException, AttributeError):
            return False
