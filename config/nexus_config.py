import time
import requests
import docker
from config.env_loader import get_env
import base64
import xml.etree.ElementTree as ET


config = get_env()
BASE_URL = config["NEXUS_URL"]
CONTAINER_NAME = "nexus"


# -----------------------------
# ENV UPDATE
# -----------------------------
def update_env(key, value, file_path="env.txt"):
    lines = []
    found = False

    with open(file_path, "r") as f:
        for line in f:
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}\n")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{key}={value}\n")

    with open(file_path, "w") as f:
        f.writelines(lines)

    print(f"✅ {key} updated in env.txt")


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
# SAFE REQUEST
# -----------------------------
def safe_request(method, url, **kwargs):
    for _ in range(5):
        try:
            return requests.request(method, url, timeout=10, **kwargs)
        except:
            time.sleep(3)
    raise Exception(f"❌ API call failed: {url}")


# -----------------------------
# WAIT FOR NEXUS
# -----------------------------
def wait_for_nexus():
    print("\n⏳ Waiting for Nexus...\n")

    for i in range(60):
        try:
            r = safe_request("GET", f"{BASE_URL}/service/rest/v1/status")
            if r.status_code in [200, 401]:
                print("✅ Nexus Ready")
                return
        except:
            pass

        print(f"Waiting... ({i+1}/60)")
        time.sleep(5)

    raise Exception("❌ Nexus not ready")


# -----------------------------
# GET INITIAL PASSWORD
# -----------------------------
def get_initial_password():
    print("\n🔑 Fetching initial password...\n")

    try:
        container = client.containers.get(CONTAINER_NAME)

        for _ in range(10):
            result = container.exec_run("cat /nexus-data/admin.password")
            pwd = result.output.decode().strip()

            if pwd:
                print("✅ Initial password fetched")
                return pwd

            time.sleep(3)

    except:
        pass

    print("ℹ️ Initial password not found (already changed)")
    return None


# -----------------------------
# ENSURE PASSWORD
# -----------------------------
def ensure_password():
    print("\n🔐 Ensuring Nexus password...\n")

    user = config["NEXUS_USER"]
    current_password = config.get("NEXUS_PASSWORD")
    new_password = config.get("NEXUS_NEW_PASSWORD")

    # -----------------------------
    # 1️⃣ TRY CURRENT PASSWORD
    # -----------------------------
    r = safe_request(
        "GET",
        f"{BASE_URL}/service/rest/v1/status",
        auth=(user, current_password)
    )

    if r.status_code == 200:
        print("✅ Logged in with NEXUS_PASSWORD")

        if new_password and new_password != current_password:
            print("🔄 Updating password → NEXUS_NEW_PASSWORD")

            r = safe_request(
                "PUT",
                f"{BASE_URL}/service/rest/v1/security/users/admin/change-password",
                auth=(user, current_password),
                headers={"Content-Type": "text/plain"},
                data=new_password
            )

            if r.status_code in [200, 204]:
                print("✅ Password updated successfully")

                update_env("NEXUS_PASSWORD", new_password)
                config["NEXUS_PASSWORD"] = new_password

                return
            else:
                raise Exception(f"❌ Password update failed: {r.text}")

        print("✅ No password change required")
        return

    # -----------------------------
    # 2️⃣ TRY NEW PASSWORD
    # -----------------------------
    if new_password:
        r = safe_request(
            "GET",
            f"{BASE_URL}/service/rest/v1/status",
            auth=(user, new_password)
        )

        if r.status_code == 200:
            print("✅ Logged in with NEXUS_NEW_PASSWORD")

            update_env("NEXUS_PASSWORD", new_password)
            config["NEXUS_PASSWORD"] = new_password

            return

    # -----------------------------
    # 3️⃣ TRY INITIAL PASSWORD
    # -----------------------------
    print("ℹ️ Trying initial password...")

    initial_pwd = get_initial_password()

    if initial_pwd:
        r = safe_request(
            "GET",
            f"{BASE_URL}/service/rest/v1/status",
            auth=(user, initial_pwd)
        )

        if r.status_code == 200:
            target = new_password if new_password else current_password

            print("🔄 First-time setup → setting password")

            r = safe_request(
                "PUT",
                f"{BASE_URL}/service/rest/v1/security/users/admin/change-password",
                auth=(user, initial_pwd),
                headers={"Content-Type": "text/plain"},
                data=target
            )

            if r.status_code in [200, 204]:
                print("✅ Password initialized successfully")

                update_env("NEXUS_PASSWORD", target)
                config["NEXUS_PASSWORD"] = target

                return

    raise Exception("❌ Unable to determine Nexus password state")


# -----------------------------
# REPO EXISTS
# -----------------------------
def repo_exists(repo_name):
    r = safe_request(
        "GET",
        f"{BASE_URL}/service/rest/v1/repositories",
        auth=(config["NEXUS_USER"], config["NEXUS_PASSWORD"])
    )

    repos = [repo["name"] for repo in r.json()]
    return repo_name in repos


# -----------------------------
# CREATE REPO
# -----------------------------
def create_maven_repo():
    repo_name = "maven-releases-custom"

    if repo_exists(repo_name):
        print("✅ Maven repo already exists")
        return repo_name

    print("\n📦 Creating Maven repository...\n")

    payload = {
        "name": repo_name,
        "online": True,
        "storage": {
            "blobStoreName": "default",
            "strictContentTypeValidation": True,
            "writePolicy": "ALLOW"
        },
        "maven": {
            "versionPolicy": "RELEASE",
            "layoutPolicy": "STRICT"
        }
    }

    r = safe_request(
        "POST",
        f"{BASE_URL}/service/rest/v1/repositories/maven/hosted",
        auth=(config["NEXUS_USER"], config["NEXUS_PASSWORD"]),
        json=payload
    )

    if r.status_code in [200, 201]:
        print("✅ Repository created")
    else:
        raise Exception(f"❌ Repo creation failed: {r.text}")

    return repo_name

# -----------------------------
# UPDATE GITHUB pom.xml
# -----------------------------
def update_pom_xml(repo_url):

    print("\n📝 Updating GitHub pom.xml...\n")

    github_user = config["GITHUB_USER"]
    github_token = config["GITHUB_TOKEN"]

    owner = "yogithak25"
    repo = "end-to-end-devops-project"

    file_path = "pom.xml"

    api_url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/contents/{file_path}"
    )

    headers = {
        "Accept": "application/vnd.github+json"
    }

    # -----------------------------
    # FETCH EXISTING pom.xml
    # -----------------------------
    r = safe_request(
        "GET",
        api_url,
        headers=headers,
        auth=(github_user, github_token)
    )

    if r.status_code != 200:
        raise Exception(f"❌ Failed fetching pom.xml: {r.text}")

    data = r.json()

    sha = data["sha"]

    # decode file content
    content = base64.b64decode(
        data["content"]
    ).decode()

    # -----------------------------
    # PARSE XML
    # -----------------------------
    root = ET.fromstring(content)

    ns = {
        "m": "http://maven.apache.org/POM/4.0.0"
    }

    distribution = root.find("m:distributionManagement", ns)

    # -----------------------------
    # CREATE distributionManagement
    # -----------------------------
    if distribution is None:

        distribution = ET.SubElement(
            root,
            "{http://maven.apache.org/POM/4.0.0}distributionManagement"
        )

    # -----------------------------
    # CREATE repository
    # -----------------------------
    repository = distribution.find("m:repository", ns)

    if repository is None:

        repository = ET.SubElement(
            distribution,
            "{http://maven.apache.org/POM/4.0.0}repository"
        )

    # -----------------------------
    # CREATE id
    # -----------------------------
    repo_id = repository.find("m:id", ns)

    if repo_id is None:

        repo_id = ET.SubElement(
            repository,
            "{http://maven.apache.org/POM/4.0.0}id"
        )

    repo_id.text = "nexus"

    # -----------------------------
    # CREATE url
    # -----------------------------
    url_element = repository.find("m:url", ns)

    if url_element is None:

        url_element = ET.SubElement(
            repository,
            "{http://maven.apache.org/POM/4.0.0}url"
        )

    current_url = (url_element.text or "").strip()

    # -----------------------------
    # IDEMPOTENT CHECK
    # -----------------------------
    if current_url == repo_url:

        print("✅ pom.xml already contains correct Nexus URL")
        return

    print(f"🔄 Updating Nexus URL")
    print(f"Old: {current_url}")
    print(f"New: {repo_url}")

    url_element.text = repo_url

    # -----------------------------
    # XML → STRING
    # -----------------------------
    # preserve original namespaces
    ET.register_namespace(
        "",
        "http://maven.apache.org/POM/4.0.0"
    )

    ET.register_namespace(
        "xsi",
        "http://www.w3.org/2001/XMLSchema-instance"
    )

    updated_xml = ET.tostring(
        root,
        encoding="unicode"
    )
    # encode to base64
    encoded_content = base64.b64encode(
        updated_xml.encode()
    ).decode()

    payload = {
        "message": "update pom.xml",
        "content": encoded_content,
        "sha": sha
    }

    # -----------------------------
    # UPDATE FILE IN GITHUB
    # -----------------------------
    r = safe_request(
        "PUT",
        api_url,
        headers=headers,
        auth=(github_user, github_token),
        json=payload
    )

    if r.status_code not in [200, 201]:
        raise Exception(f"❌ Failed updating pom.xml: {r.text}")

    print("✅ pom.xml updated directly in GitHub")


# -----------------------------
# MAIN
# -----------------------------
def setup_nexus():
    print("\n🚀 NEXUS CONFIG STARTED\n")

    wait_for_nexus()
    ensure_password()

    repo = create_maven_repo()


    repo_url = f"{BASE_URL}/repository/{repo}/"

    # UPDATE pom.xml IN GITHUB
    update_pom_xml(repo_url)

    print("\n✅ NEXUS CONFIG COMPLETED\n")

    return repo_url

