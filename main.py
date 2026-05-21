def main():

    print("\n🔥 DEVOPS AUTOMATION STARTED\n")

    # =====================================================
    # 🔹 PHASE 1: INFRA SETUP
    # =====================================================
    print("\n📦 PHASE 1: INFRASTRUCTURE SETUP\n")

    # 1️⃣ Docker Infra (Jenkins, SonarQube, Nexus)
    from docker_manager import setup_infra
    setup_infra()

    # 2️⃣ Kubernetes (k3s)
    from installer.kubernetes import install_kubernetes
    install_kubernetes()

    # 3️⃣ ArgoCD Installation (on Kubernetes)
    from installer.argocd import install_argocd
    install_argocd()

    # 4️⃣ Maven (for builds)
    from installer.maven import install_maven
    install_maven()

    # 5️⃣ Trivy (security scan)
    from installer.trivy import setup_trivy
    setup_trivy()

    print("\n✅ PHASE 1 COMPLETED (INFRA READY)\n")

    # =====================================================
    # 🔹 PHASE 2: TOOL CONFIGURATION
    # =====================================================
    print("\n⚙️ PHASE 2: TOOL CONFIGURATION\n")

    # 1️⃣ SonarQube → setup + token
    from config.sonarqube_config import setup_sonarqube
    setup_sonarqube()

    # 2️⃣ Nexus → repo + credentials
    from config.nexus_config import setup_nexus
    setup_nexus()

    # 3️⃣ Jenkins → plugins, creds, tools, sonar, nexus
    from config.jenkins_config import setup_jenkins
    setup_jenkins()

    # 4️⃣ GitHub → webhook setup
    from config.github_config import setup_github
    setup_github()

    print("\n✅ PHASE 2 COMPLETED (TOOLS CONFIGURED)\n")

    # =====================================================
    # 🔹 PHASE 3: PIPELINE SETUP
    # =====================================================
    print("\n🚀 PHASE 3: PIPELINE SETUP\n")

    from config.jenkins_pipeline import setup_pipelines
    setup_pipelines()

    print("\n✅ PHASE 3 COMPLETED (PIPELINES READY)\n")

    # =====================================================
    # 🔹 PHASE 4: CD SETUP (ARGOCD)
    # =====================================================
    print("\n🚀 PHASE 4: CONTINUOUS DEPLOYMENT (ARGOCD)\n")

    from config.argocd_config import setup_argocd
    setup_argocd()

    print("\n✅ PHASE 4 COMPLETED (ARGOCD READY)\n")

    # =====================================================
    # 🔹 PHASE 5: AUTOSCALING
    # =====================================================

    print("\n🚀 PHASE 5: AUTOSCALING SETUP\n")

    from installer.autoscaling import setup_autoscaling

    setup_autoscaling()

    print("\n✅ PHASE 5 COMPLETED (AUTOSCALING READY)\n")


    print("\n🎉 FULL END-TO-END DEVOPS AUTOMATION COMPLETED SUCCESSFULLY!\n")


if __name__ == "__main__":
    main()
