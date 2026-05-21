import docker
import time
import json

K3S_CONTAINER = "k3s-server"

client = docker.from_env()


# ------------------------------------------------
# KUBECTL
# ------------------------------------------------

def kubectl(cmd):

    container = client.containers.get(
        K3S_CONTAINER
    )

    result = container.exec_run(
        f"kubectl {cmd}"
    )

    return result.output.decode()


# ------------------------------------------------
# ENABLE HPA
# ------------------------------------------------
def enable_hpa():

    print("\n🚀 Configuring HPA...\n")

    hpas = [

        {
            "deployment": "java-devops-deployment",
            "min": 2,
            "max": 5,
            "cpu": 70
        },

        {
            "deployment": "python-devops-deployment",
            "min": 2,
            "max": 5,
            "cpu": 70
        }
    ]

    for hpa in hpas:

        deployment = hpa["deployment"]

        # ------------------------------------------------
        # CHECK EXISTING HPA
        # ------------------------------------------------
        output = kubectl(
            f"get hpa {deployment} -o json"
        )

        # ------------------------------------------------
        # HPA DOES NOT EXIST
        # ------------------------------------------------
        if "NotFound" in output:

            print(f"🚀 Creating HPA: {deployment}")

            create_cmd = f"""
            autoscale deployment {deployment}
            --cpu-percent={hpa['cpu']}
            --min={hpa['min']}
            --max={hpa['max']}
            """

            result = kubectl(create_cmd)

            print(result)

            print(f"✅ HPA created: {deployment}")

            continue

        # ------------------------------------------------
        # LOAD CURRENT CONFIG
        # ------------------------------------------------
        current = json.loads(output)

        current_min = current["spec"].get(
            "minReplicas", 1
        )

        current_max = current["spec"].get(
            "maxReplicas", 1
        )

        current_cpu = current["spec"]["metrics"][0][
            "resource"
        ]["target"]["averageUtilization"]

        # ------------------------------------------------
        # CHECK IF CHANGED
        # ------------------------------------------------
        changed = (

            current_min != hpa["min"] or
            current_max != hpa["max"] or
            current_cpu != hpa["cpu"]
        )

        # ------------------------------------------------
        # NO CHANGE
        # ------------------------------------------------
        if not changed:

            print(
                f"✅ HPA already up-to-date: "
                f"{deployment}"
            )

            continue

        # ------------------------------------------------
        # UPDATE HPA
        # ------------------------------------------------
        print(f"🔄 Updating HPA: {deployment}")

        patch_cmd = f"""
        patch hpa {deployment}
        --type merge
        -p '{{
            "spec": {{
                "minReplicas": {hpa['min']},
                "maxReplicas": {hpa['max']},
                "metrics": [
                    {{
                        "type": "Resource",
                        "resource": {{
                            "name": "cpu",
                            "target": {{
                                "type": "Utilization",
                                "averageUtilization": {hpa['cpu']}
                            }}
                        }}
                    }}
                ]
            }}
        }}'
        """

        result = kubectl(patch_cmd)

        print(result)

        print(f"✅ HPA updated: {deployment}")

# ------------------------------------------------
# INSTALL VPA
# ------------------------------------------------
def install_vpa():

    print("\n🚀 Installing VPA...\n")

    output = kubectl("get crd")

    # -----------------------------
    # ALREADY INSTALLED
    # -----------------------------
    if "verticalpodautoscalers.autoscaling.k8s.io" in output:

        print("✅ VPA already installed")

        return

    manifests = [

        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/deploy/vpa-v1-crd-gen.yaml",

        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/deploy/vpa-rbac.yaml",

        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/deploy/recommender-deployment.yaml",

        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/deploy/updater-deployment.yaml",

        "https://raw.githubusercontent.com/kubernetes/autoscaler/master/vertical-pod-autoscaler/deploy/admission-controller-deployment.yaml"
    ]

    for manifest in manifests:

        print(f"\nApplying: {manifest}\n")

        output = kubectl(
            f"apply -f {manifest}"
        )

        print(output)

    # -----------------------------
    # WAIT FOR CRD
    # -----------------------------
    print("\n⏳ Waiting for VPA CRDs...\n")

    for i in range(30):

        output = kubectl("get crd")

        if "verticalpodautoscalers.autoscaling.k8s.io" in output:

            print("✅ VPA CRDs installed")

            return

        print(f"Waiting VPA... ({i+1}/30)")

        time.sleep(2)

    raise Exception("❌ VPA installation failed")


# ------------------------------------------------
# APPLY VPA
# ------------------------------------------------
def apply_vpa():

    print("\n🚀 Applying VPA policies...\n")

    vpas = [

        {
            "name": "java-vpa",
            "deployment": "java-devops-deployment"
        },

        {
            "name": "python-vpa",
            "deployment": "python-devops-deployment"
        }
    ]

    existing = kubectl(
        "get verticalpodautoscalers.autoscaling.k8s.io"
    )

    for vpa in vpas:

        # -----------------------------
        # SKIP IF EXISTS
        # -----------------------------
        if vpa["name"] in existing:

            print(f"✅ VPA already exists: {vpa['name']}")

            continue

        yaml = f"""
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler

metadata:
  name: {vpa['name']}

spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {vpa['deployment']}

  updatePolicy:
    updateMode: Auto
"""

        container = client.containers.get(
            K3S_CONTAINER
        )

        # -----------------------------
        # CREATE YAML
        # -----------------------------
        container.exec_run(
            f"sh -c 'cat > /tmp/{vpa['name']}.yaml <<EOF\n{yaml}\nEOF'"
        )

        # -----------------------------
        # APPLY YAML
        # -----------------------------
        output = kubectl(
            f"apply -f /tmp/{vpa['name']}.yaml"
        )

        print(output)

        print(f"✅ VPA applied: {vpa['name']}")


# ------------------------------------------------
# VERIFY AUTOSCALING
# ------------------------------------------------
def verify_autoscaling():

    print("\n📊 Verifying Autoscaling...\n")

    print("\nHPA:\n")

    print(kubectl("get hpa"))

    print("\nVPA:\n")

    print(
        kubectl(
            "get verticalpodautoscalers.autoscaling.k8s.io"
        )
    )


# ------------------------------------------------
# MAIN SETUP
# ------------------------------------------------
def setup_autoscaling():

    print("\n🚀 AUTOSCALING SETUP STARTED\n")

    enable_hpa()

    install_vpa()

    # wait for vpa controllers
    time.sleep(15)

    apply_vpa()

    verify_autoscaling()

    print("\n✅ AUTOSCALING READY\n")
