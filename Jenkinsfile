def registry = 'dockerregistry.icomb.place'
def image = "${registry}/zebra-discovery"

def dockerPush(reg, img) {
    withCredentials([usernamePassword(credentialsId: 'dockerregistry.icomb.place', usernameVariable: 'REG_USER', passwordVariable: 'REG_PASS')]) {
        sh "echo \$REG_PASS | docker login ${reg} -u \$REG_USER --password-stdin"
    }
    sh "docker push ${img}"
}

pipeline {
    agent any

    stages {
        stage('Build & Push') {
            steps {
                sh "docker build -t ${image}:latest -t ${image}:\${GIT_COMMIT} ."
                script {
                    dockerPush(registry, "${image}:latest")
                    dockerPush(registry, "${image}:\${GIT_COMMIT}")
                }
            }
        }
    }

    post {
        success {
            sh 'curl -s -X POST https://docker.icomb.place/api/stacks/webhooks/8b8037ad-ddbe-43cb-a1c4-34ef4c83c694 || true'
        }
        always {
            sh "docker logout ${registry} || true"
        }
    }
}
