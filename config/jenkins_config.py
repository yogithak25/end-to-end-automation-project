import time
import requests
import json
import docker
import os
from config.env_loader import get_env

config = get_env()
BASE_URL = config["JENKINS_URL"]
CONTAINER_NAME = "jenkins"
ENV_FILE = "env.txt"

# -----------------------------
# UPDATE ENV FILE
# -----------------------------
def write_env(key, value, file_path="env.txt"):
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


def safe_add_crumb(session):
    try:
        r = session.get(f"{BASE_URL}/crumbIssuer/api/json")

        if r.status_code == 200:
            data = r.json()
            session.headers.update({
                data["crumbRequestField"]: data["crumb"]
            })
        else:
            print("ℹ️ Crumb not required")
    except Exception as e:
        raise Exception(f"❌ Failed to get Jenkins crumb: {e}")

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
    raise Exception(f"❌ API failed: {url}")


# -----------------------------
# UPDATE ENV
# -----------------------------
def update_env(key, value):
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r") as f:
            lines = f.readlines()

    found = False

    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            if line.strip() != f"{key}={value}":
                lines[i] = f"{key}={value}\n"
            found = True

    if not found:
        lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(lines)

    print(f"✅ {key} updated")


# -----------------------------
# WAIT FOR JENKINS
# -----------------------------
def wait_for_jenkins():
    print("\n⏳ Waiting for Jenkins...\n")

    for i in range(60):
        try:
            r = requests.get(f"{BASE_URL}/login")
            if r.status_code == 200:
                print("✅ Jenkins Ready")
                return
        except:
            pass

        print(f"Waiting... {i+1}/60")
        time.sleep(5)

    raise Exception("❌ Jenkins not reachable")


# -----------------------------
# INITIAL PASSWORD
# -----------------------------
def get_initial_password():
    try:
        container = client.containers.get(CONTAINER_NAME)
        result = container.exec_run(
            "cat /var/jenkins_home/secrets/initialAdminPassword"
        )
        return result.output.decode().strip()
    except:
        return None


# -----------------------------
# LOGIN CHECK
# -----------------------------
def can_login(user, pwd):
    try:
        r = requests.get(f"{BASE_URL}/api/json", auth=(user, pwd))
        return r.status_code == 200
    except:
        return False


# -----------------------------
# ENSURE PASSWORD
# -----------------------------
def ensure_password():
    print("\n🔐 Ensuring Jenkins password...\n")

    user = config["JENKINS_USER"]
    current_pwd = config.get("JENKINS_PASSWORD")
    new_pwd = config.get("JENKINS_NEW_PASSWORD")

    # -----------------------------
    # 1️⃣ TRY CURRENT PASSWORD
    # -----------------------------
    if can_login(user, current_pwd):
        print("✅ Logged in with JENKINS_PASSWORD")

        # Update only if new password provided
        if new_pwd and new_pwd != current_pwd:
            print("🔄 Updating password → JENKINS_NEW_PASSWORD")

            session = requests.Session()
            session.auth = (user, current_pwd)

            safe_add_crumb(session)

            script = f"""
import jenkins.model.*
import hudson.security.*

def instance = Jenkins.instance
def realm = instance.getSecurityRealm()

realm.createAccount("{user}", "{new_pwd}")

instance.save()
"""

            r = session.post(f"{BASE_URL}/scriptText", data={"script": script})

            if r.status_code not in [200, 201]:
                raise Exception(f"❌ Password update failed: {r.text}")

            print("✅ Password updated successfully")

            # 🔥 persist + runtime sync
            write_env("JENKINS_PASSWORD", new_pwd)
            config["JENKINS_PASSWORD"] = new_pwd

            return

        print("✅ No password change required")
        return

    # -----------------------------
    # 2️⃣ TRY NEW PASSWORD
    # -----------------------------
    if new_pwd and can_login(user, new_pwd):
        print("✅ Logged in with JENKINS_NEW_PASSWORD")

        # 🔥 promote new → current
        write_env("JENKINS_PASSWORD", new_pwd)
        config["JENKINS_PASSWORD"] = new_pwd

        return

    # -----------------------------
    # 3️⃣ FIRST RUN (INITIAL PASSWORD)
    # -----------------------------
    initial_pwd = get_initial_password()

    if initial_pwd:
        print("🔄 First-time setup → setting password")

        session = requests.Session()
        session.auth = (user, initial_pwd)
        safe_add_crumb(session)

        script = f"""
import jenkins.model.*
import hudson.security.*

def instance = Jenkins.instance

def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount("{user}", "{current_pwd}")

instance.setSecurityRealm(realm)

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
instance.setAuthorizationStrategy(strategy)

instance.save()
"""

        r = session.post(f"{BASE_URL}/scriptText", data={"script": script})

        if r.status_code not in [200, 201]:
            raise Exception(f"❌ Initial setup failed: {r.text}")

        print("✅ Password initialized successfully")

        # 🔥 persist + runtime sync
        write_env("JENKINS_PASSWORD", current_pwd)
        config["JENKINS_PASSWORD"] = current_pwd

        # restart required once
        client.containers.get(CONTAINER_NAME).restart()
        wait_for_jenkins()

        return

    # -----------------------------
    # 4️⃣ FAIL
    # -----------------------------
    raise Exception("❌ Unable to determine Jenkins password state")


# -----------------------------
# GENERATE TOKEN
# -----------------------------
def generate_token():
    print("\n🔑 Generating Jenkins token...\n")

    user = config["JENKINS_USER"]
    pwd = config["JENKINS_PASSWORD"]

    token = config.get("JENKINS_TOKEN")

    if token and can_login(user, token):
        print("✅ Token already valid")
        return token

    session = requests.Session()
    session.auth = (user, pwd)

    crumb = session.get(f"{BASE_URL}/crumbIssuer/api/json").json()
    session.headers.update({crumb["crumbRequestField"]: crumb["crumb"]})

    r = session.post(
        f"{BASE_URL}/user/{user}/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
        data={"newTokenName": "devops-token"}
    )

    token = r.json()["data"]["tokenValue"]

    update_env("JENKINS_TOKEN", token)

    print("✅ Token generated")

    return token


# -----------------------------
# INSTALL PLUGINS
# -----------------------------
def install_plugins():

    print("\n📦 Ensuring Jenkins plugins...\n")

    plugins = [
        "workflow-aggregator",
        "git",
        "github",
        "pipeline-stage-view",
        "docker-workflow",
        "kubernetes",
        "sonar",
        "config-file-provider",
        "maven-plugin",
        "pipeline-maven"
    ]

    user = config["JENKINS_USER"]
    password = config["JENKINS_PASSWORD"]

    session = requests.Session()
    session.auth = (user, password)

    # -----------------------------
    # WAIT FOR PLUGIN MANAGER
    # -----------------------------
    print("⏳ Waiting for Jenkins Plugin Manager...\n")

    installed = []

    for i in range(60):

        print(f"Waiting Plugin Manager... ({i+1}/60)")

        try:

            r = session.get(
                f"{BASE_URL}/pluginManager/api/json?depth=1",
                timeout=10
            )

            # Jenkins still initializing
            if r.status_code != 200:
                time.sleep(5)
                continue

            # sometimes Jenkins returns HTML while loading
            if "application/json" not in r.headers.get("Content-Type", ""):
                time.sleep(5)
                continue

            data = r.json()

            # plugin manager loaded
            if data.get("plugins") is not None:

                installed = [
                    p["shortName"]
                    for p in data.get("plugins", [])
                ]

                break

        except:
            pass

        time.sleep(5)

    # -----------------------------
    # VALIDATE
    # -----------------------------
    if installed is None:
        raise Exception("❌ Jenkins Plugin Manager not ready")

    print(f"\n✅ Installed plugins found: {len(installed)}")

    # -----------------------------
    # FIND MISSING PLUGINS
    # -----------------------------
    missing = [
        p for p in plugins
        if p not in installed
    ]

    if not missing:
        print("✅ All plugins already installed")
        return

    print(f"\n⬇️ Missing plugins: {missing}\n")

    # -----------------------------
    # GET CRUMB
    # -----------------------------
    crumb = None

    for i in range(30):

        try:

            r = session.get(
                f"{BASE_URL}/crumbIssuer/api/json",
                timeout=10
            )

            if (
                r.status_code == 200 and
                "application/json" in r.headers.get("Content-Type", "")
            ):
                crumb = r.json()
                break

        except:
            pass

        print(f"Waiting Crumb... ({i+1}/30)")
        time.sleep(3)

    if not crumb:
        raise Exception("❌ Failed to get Jenkins crumb")

    session.headers.update({
        crumb["crumbRequestField"]: crumb["crumb"]
    })

    restart_required = False

    # -----------------------------
    # INSTALL MISSING PLUGINS
    # -----------------------------
    for plugin in missing:

        print(f"\n⬇️ Installing plugin: {plugin}")

        xml = f"""
<jenkins>
  <install plugin="{plugin}@latest" />
</jenkins>
"""

        r = session.post(
            f"{BASE_URL}/pluginManager/installNecessaryPlugins",
            headers={"Content-Type": "text/xml"},
            data=xml
        )

        if r.status_code not in [200, 201, 202]:
            raise Exception(f"❌ Failed installing {plugin}")

        installed_ok = False

        # -----------------------------
        # WAIT FOR INSTALLATION
        # -----------------------------
        for i in range(60):

            try:

                rr = session.get(
                    f"{BASE_URL}/pluginManager/api/json?depth=1",
                    timeout=10
                )

                if rr.status_code != 200:
                    time.sleep(5)
                    continue

                if "application/json" not in rr.headers.get("Content-Type", ""):
                    time.sleep(5)
                    continue

                data = rr.json()

                current_plugins = [
                    p["shortName"]
                    for p in data.get("plugins", [])
                ]

                if plugin in current_plugins:

                    print(f"✅ Installed: {plugin}")

                    installed_ok = True
                    restart_required = True
                    break

            except:
                pass

            print(f"Installing {plugin}... ({i+1}/60)")
            time.sleep(5)

        if not installed_ok:
            raise Exception(f"❌ Timeout installing: {plugin}")

    # -----------------------------
    # RESTART ONLY IF REQUIRED
    # -----------------------------
    if restart_required:

        print("\n🔄 Restarting Jenkins...\n")

        client.containers.get(CONTAINER_NAME).restart()

        wait_for_jenkins()

        print("✅ Jenkins restarted")

    print("\n✅ Plugin setup completed\n")

# -----------------------------
# ADD CREDENTIALS
# -----------------------------
def add_credentials():
    print("\n🔐 Ensuring Jenkins credentials...\n")

    user = config["JENKINS_USER"]
    password = config["JENKINS_PASSWORD"]

    # -----------------------------
    # USE SESSION
    # -----------------------------
    session = requests.Session()
    session.auth = (user, password)

    creds_url = f"{BASE_URL}/credentials/store/system/domain/_/api/json?tree=credentials[id]"

    # -----------------------------
    # WAIT FOR API READY
    # -----------------------------
    print("⏳ Waiting for credentials API...\n")

    res_json = None

    for i in range(30):
        try:
            r = session.get(creds_url, timeout=5)

            if r.status_code == 200 and "application/json" in r.headers.get("Content-Type", ""):
                res_json = r.json()
                print("✅ Credentials API ready")
                break
            else:
                print(f"Waiting... {i+1}/30")

        except:
            pass

        time.sleep(5)

    if not res_json:
        raise Exception("❌ Credentials API not ready")

    existing_ids = [c["id"] for c in res_json.get("credentials", [])]

    # -----------------------------
    # GET CRUMB (same session)
    # -----------------------------
    crumb = session.get(f"{BASE_URL}/crumbIssuer/api/json").json()

    session.headers.update({
        crumb["crumbRequestField"]: crumb["crumb"]
    })

    create_url = f"{BASE_URL}/credentials/store/system/domain/_/createCredentials"
    update_url = f"{BASE_URL}/credentials/store/system/domain/_/credential/{{cid}}/config.xml"

    def ensure_credential(cid, user_val, pwd_val):

        if cid in existing_ids:
            print(f"🔄 Updating {cid}...")

            xml = f"""
<com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl>
  <scope>GLOBAL</scope>
  <id>{cid}</id>
  <description>{cid}</description>
  <username>{user_val}</username>
  <password>{pwd_val}</password>
</com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl>
"""

            session.post(
                update_url.format(cid=cid),
                headers={"Content-Type": "application/xml"},
                data=xml
            )

            print(f"✅ {cid} updated")
            return

        print(f"➕ Creating {cid}...")

        payload = {
            "": "0",
            "credentials": {
                "scope": "GLOBAL",
                "id": cid,
                "username": user_val,
                "password": pwd_val,
                "description": cid,
                "$class": "com.cloudbees.plugins.credentials.impl.UsernamePasswordCredentialsImpl"
            }
        }

        session.post(
            create_url,
            data={"json": json.dumps(payload)}
        )

        print(f"✅ {cid} created")

    # APPLY
    ensure_credential("github-cred", config["GITHUB_USER"], config["GITHUB_TOKEN"])
    ensure_credential("dockerhub-cred", config["DOCKER_USER"], config["DOCKER_PASS"])
    ensure_credential("nexus-cred", config["NEXUS_USER"], config["NEXUS_PASSWORD"])

    print("\n✅ Credentials setup completed\n")

# -----------------------------
# SONAR TOKEN CREDENTIAL
# -----------------------------
def ensure_sonar_token_credential():
    print("🔐 Ensuring sonar-token credential...")

    user = config["JENKINS_USER"]
    password = config["JENKINS_PASSWORD"]

    session = requests.Session()
    session.auth = (user, password)

    creds_url = f"{BASE_URL}/credentials/store/system/domain/_/api/json?tree=credentials[id]"

    # -----------------------------
    # WAIT FOR API READY
    # -----------------------------
    print("⏳ Waiting for credentials API (sonar)...")

    res_json = None

    for i in range(30):
        try:
            r = session.get(creds_url, timeout=5)

            if r.status_code == 200 and "application/json" in r.headers.get("Content-Type", ""):
                res_json = r.json()
                print("✅ Credentials API ready")
                break
            else:
                print(f"Waiting... {i+1}/30")

        except:
            pass

        time.sleep(5)

    if not res_json:
        raise Exception("❌ Credentials API not ready")

    existing_ids = [c["id"] for c in res_json.get("credentials", [])]

    # -----------------------------
    # GET CRUMB (same session)
    # -----------------------------
    crumb = session.get(f"{BASE_URL}/crumbIssuer/api/json").json()

    session.headers.update({
        crumb["crumbRequestField"]: crumb["crumb"]
    })

    # -----------------------------
    # CREATE OR UPDATE
    # -----------------------------
    if "sonar-token" in existing_ids:
        print("🔄 Updating sonar-token...")

        xml = f"""
<org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl>
  <scope>GLOBAL</scope>
  <id>sonar-token</id>
  <description>sonar-token</description>
  <secret>{config["SONAR_TOKEN"]}</secret>
</org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl>
"""

        session.post(
            f"{BASE_URL}/credentials/store/system/domain/_/credential/sonar-token/config.xml",
            headers={"Content-Type": "application/xml"},
            data=xml
        )

        print("✅ sonar-token updated")
        return

    print("➕ Creating sonar-token...")

    payload = {
        "": "0",
        "credentials": {
            "scope": "GLOBAL",
            "id": "sonar-token",
            "secret": config["SONAR_TOKEN"],
            "description": "sonar-token",
            "$class": "org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl"
        }
    }

    session.post(
        f"{BASE_URL}/credentials/store/system/domain/_/createCredentials",
        data={"json": json.dumps(payload)}
    )

    print("✅ sonar-token created")


# -----------------------------
# CONFIGURE TOOLS
# -----------------------------
def configure_tools():
    print("\n⚙️ Ensuring Maven + Sonar tools...\n")

    script = """
import jenkins.model.*
import hudson.tasks.Maven
import hudson.tasks.Maven.MavenInstaller
import hudson.tools.InstallSourceProperty
import hudson.plugins.sonar.*

def jenkins = Jenkins.instance

// -----------------------------
// MAVEN CONFIG
// -----------------------------
def mavenDesc = jenkins.getDescriptorByType(Maven.DescriptorImpl)
def mavenList = mavenDesc.installations as List

def mavenExists = mavenList.find { it.name == "maven-3" }

if (mavenExists != null) {
    println("✅ Maven already configured")
} else {
    println("🔄 Configuring Maven...")

    def installer = new MavenInstaller("3.9.9")
    def prop = new InstallSourceProperty([installer])
    def maven = new Maven.MavenInstallation("maven-3", "", [prop])

    mavenList.add(maven)
    mavenDesc.setInstallations(mavenList as Maven.MavenInstallation[])
    mavenDesc.save()

    println("✅ Maven configured")
}

// -----------------------------
// SONAR SCANNER CONFIG
// -----------------------------
def sonarDesc = jenkins.getDescriptorByType(SonarRunnerInstallation.DescriptorImpl)
def sonarList = sonarDesc.installations as List

def sonarExists = sonarList.find { it.name == "sonar-scanner" }

if (sonarExists != null) {
    println("✅ Sonar Scanner already configured")
} else {
    println("🔄 Configuring Sonar Scanner...")

    def installer = new SonarRunnerInstaller("8.1.0.6389")
    def prop = new InstallSourceProperty([installer])
    def sonar = new SonarRunnerInstallation("sonar-scanner", "", [prop])

    sonarList.add(sonar)
    sonarDesc.setInstallations(sonarList as SonarRunnerInstallation[])
    sonarDesc.save()

    println("✅ Sonar Scanner configured")
}

println("⚙️ Tool configuration ensured")
"""
    print(run_groovy(script))


# -----------------------------
# CONFIGURE SONAR SERVER
# -----------------------------
def configure_sonar():

    print("\n🔗 Configuring SonarQube...\n")

    script = f"""
import jenkins.model.*
import hudson.plugins.sonar.*
import org.jenkinsci.plugins.structs.describable.DescribableModel

def desc = Jenkins.instance.getDescriptorByType(SonarGlobalConfiguration.class)

// -----------------------------
// CHECK IF CORRECT ALREADY
// -----------------------------
def existing = desc.installations.find {{
    it.name == "sonarqube" &&
    it.serverUrl == "{config['SONAR_URL']}" &&
    it.credentialsId == "sonar-token"
}}

if (existing != null) {{
    println("SonarQube already configured correctly")
    return
}}

// -----------------------------
// CREATE CORRECT OBJECT
// -----------------------------
def model = DescribableModel.of(SonarInstallation)

def instance = model.instantiate([
    name: "sonarqube",
    serverUrl: "{config['SONAR_URL']}",
    credentialsId: "sonar-token"
])


desc.setInstallations([instance] as SonarInstallation[])
desc.save()

println("SonarQube configured correctly")
"""
    print(run_groovy(script))



# -----------------------------
# CONFIGURE NEXUS
# -----------------------------
def configure_nexus_settings():
    print("\n📦 Configuring Nexus Maven settings...\n")

    container = client.containers.get(CONTAINER_NAME)

    xml = f"""<settings>
  <servers>
    <server>
      <id>nexus</id>
      <username>{config["NEXUS_USER"]}</username>
      <password>{config["NEXUS_PASSWORD"]}</password>
    </server>
  </servers>
</settings>"""

    # -----------------------------
    # ENSURE DIRECTORY EXISTS
    # -----------------------------
    container.exec_run("mkdir -p /var/jenkins_home/.m2")

    # -----------------------------
    # CHECK IF FILE EXISTS
    # -----------------------------
    result = container.exec_run(
        "cat /var/jenkins_home/.m2/settings.xml",
        stderr=False
    )

    if result.exit_code == 0:
        existing_xml = result.output.decode().strip()

        if existing_xml == xml.strip():
            print("✅ Nexus Maven settings already configured")
            return
        else:
            print("🔄 Nexus settings found but different → updating...")

    else:
        print("➕ Creating Nexus Maven settings...")

    # -----------------------------
    # WRITE FILE (UPDATE OR CREATE)
    # -----------------------------
    container.exec_run(
        f"bash -c 'cat > /var/jenkins_home/.m2/settings.xml <<EOF\n{xml}\nEOF'"
    )

    print("✅ Nexus Maven settings configured\n")

# -----------------------------
# RUN GROOVY
# -----------------------------
def run_groovy(script):
    user = config["JENKINS_USER"]
    password = config["JENKINS_PASSWORD"]   # 🔥 USE PASSWORD

    session = requests.Session()
    session.auth = (user, password)

    # -----------------------------
    # WAIT FOR JENKINS READY
    # -----------------------------
    print("⏳ Waiting for Groovy API readiness...")

    for i in range(20):
        try:
            r = session.get(f"{BASE_URL}/api/json", timeout=5)

            if r.status_code == 200:
                break
        except:
            pass

        print(f"Waiting... {i+1}/20")
        time.sleep(3)

    # -----------------------------
    # GET CRUMB (same session)
    # -----------------------------
    crumb = None

    for i in range(20):
        try:
            r = session.get(f"{BASE_URL}/crumbIssuer/api/json", timeout=5)

            if r.status_code == 200 and "application/json" in r.headers.get("Content-Type", ""):
                crumb = r.json()
                break
        except:
            pass

        time.sleep(2)

    if not crumb:
        raise Exception("❌ Failed to get crumb")

    session.headers.update({
        crumb["crumbRequestField"]: crumb["crumb"]
    })

    # -----------------------------
    # EXECUTE GROOVY
    # -----------------------------
    r = session.post(
        f"{BASE_URL}/scriptText",
        data={"script": script}
    )

    if r.status_code not in [200, 201]:
        raise Exception(f"❌ Groovy execution failed: {r.text}")

    return r.text

# -----------------------------
# MAIN
# -----------------------------
def setup_jenkins():
    print("\n🚀 JENKINS FULL CONFIG STARTED\n")

    wait_for_jenkins()
    ensure_password()
    generate_token()

    client.containers.get(CONTAINER_NAME).restart()
    wait_for_jenkins()

    install_plugins()

    add_credentials()
    ensure_sonar_token_credential()

    configure_tools()
    configure_sonar()
    configure_nexus_settings()

    print("\n✅ JENKINS FULLY CONFIGURED\n")

