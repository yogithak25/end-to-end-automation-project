import docker
import time
import os
import requests
from config.env_loader import get_env

DATA_ROOT = "/opt/devops-data"
client = docker.from_env()


# -----------------------------
# FIX SONAR SYSCTL
# -----------------------------
def fix_sonar_sysctl():
    print("🔧 Fixing SonarQube system limits...")

    if os.system("which sysctl > /dev/null 2>&1") != 0:
        print("⚠️ sysctl not available (skip)")
        return

    os.system("sysctl -w vm.max_map_count=262144")
    print("✅ vm.max_map_count set")


# -----------------------------
# CREATE DIRECTORIES
# -----------------------------
def ensure_data_dirs():
    paths = [
        f"{DATA_ROOT}/jenkins",
        f"{DATA_ROOT}/sonar/data",
        f"{DATA_ROOT}/sonar/extensions",
        f"{DATA_ROOT}/sonar/logs",
        f"{DATA_ROOT}/nexus"
    ]

    for p in paths:
        os.makedirs(p, exist_ok=True)

    print("✅ Directories ensured")


# -----------------------------
# FIX PERMISSIONS
# -----------------------------
def fix_permissions():

    print("🔧 Fixing permissions...")

    paths = [
        ("/data/jenkins", "1000:1000", "775"),

        ("/data/sonar/data", "1000:0", "777"),
        ("/data/sonar/extensions", "1000:0", "777"),
        ("/data/sonar/logs", "1000:0", "777"),

        ("/data/nexus", "200:200", "775")
    ]

    for path, uid_gid, perms in paths:

        try:

            result = client.containers.run(
                image="busybox",

                user="0:0",

                command=(
                    f"sh -c "
                    f"'mkdir -p {path} && "
                    f"chown -R {uid_gid} {path} && "
                    f"chmod -R {perms} {path} && "
                    f"ls -ld {path}'"
                ),

                volumes={
                    DATA_ROOT: {
                        "bind": "/data",
                        "mode": "rw"
                    }
                },

                privileged=True,

                remove=True
            )

            print(result.decode().strip())

            print(f"✅ Fixed {path}")

        except Exception as e:
            print(f"⚠️ Skipped {path}: {e}")

# -----------------------------
# CHECK CONTAINER
# -----------------------------
def container_exists(name):
    try:
        client.containers.get(name)
        return True
    except:
        return False


# -----------------------------
# JENKINS IMAGE
# -----------------------------
def ensure_jenkins_image():
    name = "jenkins-docker"

    try:
        client.images.get(name)
        return name
    except:
        pass

    print("🚀 Building Jenkins image...")

    docker_gid = os.stat("/var/run/docker.sock").st_gid

    dockerfile = f"""
    FROM jenkins/jenkins:lts
    USER root
    RUN apt-get update && apt-get install -y docker.io
    RUN groupdel docker || true
    RUN groupadd -g {docker_gid} docker
    RUN usermod -aG docker jenkins
    USER jenkins
    """

    import io, tarfile
    file_obj = io.BytesIO()

    with tarfile.open(fileobj=file_obj, mode='w') as tar:
        data = dockerfile.encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    file_obj.seek(0)

    client.images.build(fileobj=file_obj, custom_context=True, tag=name)
    return name


# -----------------------------
# START JENKINS
# -----------------------------
def ensure_jenkins():
    if container_exists("jenkins"):
        c = client.containers.get("jenkins")
        if c.status != "running":
            print("🔄 Starting Jenkins...")
            c.start()
        return

    print("🚀 Creating Jenkins...")

    client.containers.run(
        ensure_jenkins_image(),
        name="jenkins",
        detach=True,
        ports={"8080/tcp": 8080},
        volumes={
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            f"{DATA_ROOT}/jenkins": {"bind": "/var/jenkins_home", "mode": "rw"}
        }
    )


# -----------------------------
# START SONAR
# -----------------------------
def ensure_sonarqube():
    if container_exists("sonarqube"):
        c = client.containers.get("sonarqube")
        if c.status != "running":
            print("🔄 Starting SonarQube...")
            c.start()
        return

    print("🚀 Creating SonarQube...")

    client.containers.run(
        "sonarqube:lts",
        name="sonarqube",
        detach=True,
        ports={"9000/tcp": 9000},
        environment={"SONAR_ES_JAVA_OPTS": "-Xms512m -Xmx512m"},
        volumes={
            f"{DATA_ROOT}/sonar/data": {"bind": "/opt/sonarqube/data", "mode": "rw"},
            f"{DATA_ROOT}/sonar/extensions": {"bind": "/opt/sonarqube/extensions", "mode": "rw"},
            f"{DATA_ROOT}/sonar/logs": {"bind": "/opt/sonarqube/logs", "mode": "rw"},
        }
    )


# -----------------------------
# START NEXUS
# -----------------------------
def ensure_nexus():
    if container_exists("nexus"):
        c = client.containers.get("nexus")
        if c.status != "running":
            print("🔄 Starting Nexus...")
            c.start()
        return

    print("🚀 Creating Nexus...")
    client.containers.run(
        "sonatype/nexus3",
        name="nexus",
        detach=True,
        ports={"8081/tcp": 8081},
        volumes={
            f"{DATA_ROOT}/nexus": {"bind": "/nexus-data", "mode": "rw"}
        }
    )


# -----------------------------
# WAIT FOR JENKINS
# -----------------------------
def wait_for_jenkins():
    env = get_env()
    url = f"{env['JENKINS_URL']}/login"

    print("\n⏳ Waiting for Jenkins...\n")

    for i in range(60):
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                print("✅ Jenkins ready")
                return
        except:
            pass

        print(f"Waiting Jenkins... ({i+1}/60)")
        time.sleep(5)

    logs = client.containers.get("jenkins").logs().decode()
    raise Exception(f"❌ Jenkins failed:\n{logs}")


# -----------------------------
# WAIT FOR SONAR
# -----------------------------
def wait_for_sonar():
    env = get_env()
    url = f"{env['SONAR_URL']}/api/system/status"

    print("\n⏳ Waiting for SonarQube...\n")

    for i in range(60):
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                status = r.json().get("status")
                print(f"Sonar status: {status}")

                if status == "UP":
                    print("✅ SonarQube ready")
                    return
        except Exception as e:
            print(f"Waiting Sonar... ({i+1}/60)")

        time.sleep(5)

    logs = client.containers.get("sonarqube").logs().decode()
    raise Exception(f"❌ Sonar failed:\n{logs}")


# -----------------------------
# WAIT FOR NEXUS
# -----------------------------
def wait_for_nexus():
    env = get_env()
    url = f"{env['NEXUS_URL']}"

    print("\n⏳ Waiting for Nexus...\n")

    for i in range(60):
        try:
            r = requests.get(url, timeout=5)
            if r.status_code in [200, 401]:
                print("✅ Nexus ready")
                return
        except:
            pass

        time.sleep(5)

    raise Exception("❌ Nexus not ready")


# -----------------------------
# MAIN SETUP
# -----------------------------
def setup_infra():
    print("\n🔥 Starting DevOps Infra...\n")

    fix_sonar_sysctl()
    ensure_data_dirs()

    fix_permissions()
    # start containers FIRST
    ensure_jenkins()
    ensure_sonarqube()
    ensure_nexus()

    # wait a bit to create volume structure
    time.sleep(15)


    # wait services
    wait_for_jenkins()
    wait_for_sonar()
    wait_for_nexus()

    print("\n✅ Infra Ready\n")

