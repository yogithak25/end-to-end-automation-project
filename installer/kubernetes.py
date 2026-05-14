import time
import docker
import os
from config.env_loader import get_env

config = get_env()
CONTAINER_NAME = "k3s-server"
DATA_PATH = "/opt/devops-data/k3s"


# -----------------------------
# DOCKER CLIENT
# -----------------------------
def get_client():
    try:
        return docker.from_env()
    except:
        return docker.DockerClient(base_url="npipe://./pipe/docker_engine")


client = get_client()


# -----------------------------
# ENSURE DATA DIR
# -----------------------------
def ensure_k3s_dir():
    os.makedirs(DATA_PATH, exist_ok=True)
    print("✅ k3s data directory ensured")


# -----------------------------
# FIX PERMISSIONS
# -----------------------------
def fix_k3s_permissions():
    print("🔧 Fixing k3s permissions...")

    try:
        client.containers.run(
            "busybox",
            command="sh -c 'chmod -R 755 /data/k3s'",
            volumes={"/opt/devops-data": {"bind": "/data", "mode": "rw"}},
            remove=True
        )
        print("✅ k3s permissions fixed")
    except Exception as e:
        print(f"⚠️ Permission fix skipped: {e}")


# -----------------------------
# GET CONTAINER
# -----------------------------
def get_container():
    try:
        return client.containers.get(CONTAINER_NAME)
    except:
        return None


# -----------------------------
# DELETE CONTAINER
# -----------------------------
def delete_container(container):
    if container:
        print("⚠️ Removing existing k3s container...")
        container.remove(force=True)
        print("✅ Removed")


# -----------------------------
# CREATE CLUSTER (WITH VOLUME)
# -----------------------------
def create_cluster():

    print("\n☸️ Creating Kubernetes cluster (k3s)...\n")

    return client.containers.run(
        "rancher/k3s:v1.30.0-k3s1",
        name=CONTAINER_NAME,
        privileged=True,
        detach=True,
        restart_policy={"Name": "always"},
        ports={
            "6443/tcp": 6443,
            "32578/tcp": 32578,
            "30007/tcp": 30007,
            "30008/tcp": 30008,
        },
        volumes={
            DATA_PATH: {
                "bind": "/var/lib/rancher/k3s",
                "mode": "rw"
            }
        },
        command="server --node-name k3s-master"
    )


# -----------------------------
# WAIT FOR K8S READY
# -----------------------------
def wait_for_ready(container):

    print("\n⏳ Waiting for Kubernetes...\n")

    for i in range(60):
        try:
            result = container.exec_run("kubectl get nodes")
            output = result.output.decode()

            if "Ready" in output:
                print("✅ Kubernetes Ready")
                return

        except:
            pass

        print(f"Waiting... ({i+1}/60)")
        time.sleep(5)

    logs = container.logs().decode()
    raise Exception(f"❌ Kubernetes not ready:\n{logs}")


# -----------------------------
# VALIDATE PORTS
# -----------------------------
def ports_correct(container):

    container.reload()
    ports = container.attrs['NetworkSettings']['Ports']

    required = ["6443/tcp", "32578/tcp", "30007/tcp", "30008/tcp"]

    return all(ports.get(p) is not None for p in required)


# -----------------------------
# PRINT ACCESS
# -----------------------------
def print_access():

    ip = config["EC2_IP"]

    print("\n🌐 Kubernetes Access:\n")
    print(f"K8s API       → https://{ip}:6443")
    print(f"ArgoCD UI     → https://{ip}:32578")
    print(f"NodePort Apps → http://{ip}:30007 / 30008")


# -----------------------------
# MAIN FUNCTION
# -----------------------------
def install_kubernetes():

    print("\n🚀 Kubernetes Setup Started\n")

    # 1. Ensure persistence directory
    ensure_k3s_dir()

    # 2. Handle container
    container = get_container()

    if container:
        if not ports_correct(container):
            delete_container(container)
            container = create_cluster()
        else:
            if container.status != "running":
                print("🔄 Starting Kubernetes...")
                container.start()
            else:
                print("✅ Kubernetes already running")
    else:
        container = create_cluster()


    # allow k3s to create internal dirs
    time.sleep(10)

    # 3. Fix permissions AFTER dirs exist
    fix_k3s_permissions()

    # 4. Wait for ready
    wait_for_ready(container)

    # 5. Print access
    print_access()

    print("\n✅ Kubernetes READY\n")

