"""Vagrant VM management methods for LitmusSetup (mixin)."""

import os
import subprocess
import time
from pathlib import Path
from typing import Optional


class _VagrantMixin:
    """Vagrant and libvirt methods mixed into LitmusSetup."""

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
        """Create a Vagrantfile for local cluster VMs."""
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
    ) -> Path:
        """Provision a cluster from a scenario's cluster configuration."""
        cp = cluster_config.get("control_plane", {})
        workers = cluster_config.get("workers", {})
        num_workers = workers.get("count", 2)

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

        self.vagrant_up(vagrant_dir, provider="libvirt")
        return vagrant_dir

    def _recover_shutoff_libvirt_vms(self, vagrant_dir: Path) -> list[str]:
        """Detect and start shutoff libvirt VMs via virsh.

        Works around a vagrant-libvirt bug where `vagrant up` fails with
        'virDomainSetMemory: domain is not running' for shutoff VMs.

        Returns:
            List of VM names that were recovered.
        """
        env = self._get_vagrant_env()
        try:
            result = subprocess.run(
                ["vagrant", "status", "--machine-readable"],
                capture_output=True,
                text=True,
                cwd=str(vagrant_dir),
                env=env,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

        shutoff_vms = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split(",")
            if len(parts) >= 4 and parts[2] == "state" and parts[3] == "shutoff":
                shutoff_vms.append(parts[1])

        if not shutoff_vms:
            return []

        # vagrant-libvirt names domains as <project_dir>_<vm>
        project_name = vagrant_dir.name
        recovered = []
        for vm_name in shutoff_vms:
            # vagrant-libvirt names domains as <project>_<vm>
            domain_name = f"{project_name}_{vm_name}"
            try:
                subprocess.run(
                    ["virsh", "start", domain_name],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                recovered.append(vm_name)
                print(f"  Recovered shutoff VM: {vm_name} (virsh start {domain_name})")
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Domain name might differ; try without project prefix
                try:
                    subprocess.run(
                        ["virsh", "start", vm_name],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    recovered.append(vm_name)
                    print(f"  Recovered shutoff VM: {vm_name}")
                except (subprocess.CalledProcessError, FileNotFoundError):
                    pass

        if recovered:
            print(f"  Waiting for {len(recovered)} recovered VM(s) to boot...")
            time.sleep(30)

        return recovered

    def vagrant_up(
        self,
        vagrant_dir: Path,
        provider: str = "libvirt",
    ) -> bool:
        """Start Vagrant VMs."""
        vagrant_dir = Path(vagrant_dir)

        if not (vagrant_dir / "Vagrantfile").exists():
            raise RuntimeError(f"No Vagrantfile found in {vagrant_dir}")

        # Recover shutoff libvirt VMs before vagrant up to avoid
        # the virDomainSetMemory bug in vagrant-libvirt
        self._recover_shutoff_libvirt_vms(vagrant_dir)

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
        """Destroy Vagrant VMs."""
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
        """Get status of Vagrant VMs."""
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
        """Get SSH configuration for all Vagrant VMs."""
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
        """Fetch kubeconfig from a Vagrant control plane VM."""
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
        """Deploy a Kubernetes cluster on Vagrant VMs."""
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
