// CI/CD for the note-taking agent.
// Every branch: build the image, lint, and boot the full stack (RabbitMQ, Redis, gateway, workers)
// as a throwaway test project. On main only: redeploy the long-running stack on this Jenkins host.
//
// Jenkins needs: Docker + the Compose plugin (2.24.4+) on the agent, and a "Secret file"
// credential with ID `note-agent-env` holding the production .env.
pipeline {
  agent any

  options {
    disableConcurrentBuilds()
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  environment {
    IMAGE_TAG    = "${env.BUILD_NUMBER}"
    CI_COMPOSE   = "-p note-agent-ci-${env.BUILD_NUMBER} -f docker-compose.yml -f ci/docker-compose.ci.yml"
    PROD_COMPOSE = "-p note-agent -f docker-compose.yml"
  }

  stages {
    stage('Build image') {
      steps {
        sh 'docker build -t note-agent:$IMAGE_TAG .'
      }
    }

    stage('Lint') {
      steps {
        // Rules come from ruff.toml: syntax errors, undefined names, unused imports.
        sh 'docker run --rm note-agent:$IMAGE_TAG sh -c "pip install -q --root-user-action=ignore ruff==0.17.5 && ruff check ."'
      }
    }

    stage('Smoke test') {
      steps {
        sh 'docker compose $CI_COMPOSE up -d'
        sh 'sh ci/wait-healthy.sh $CI_COMPOSE'
      }
      post {
        failure { sh 'docker compose $CI_COMPOSE logs --tail 100' }
        always  { sh 'docker compose $CI_COMPOSE down -v --remove-orphans' }
      }
    }

    stage('Package extension') {
      steps {
        archiveArtifacts artifacts: 'extension/**', fingerprint: true
      }
    }

    stage('Deploy') {
      // BRANCH_NAME is set in Multibranch jobs, GIT_BRANCH in plain Pipeline jobs.
      when { expression { ['main', 'origin/main'].contains(env.BRANCH_NAME ?: env.GIT_BRANCH) } }
      steps {
        withCredentials([file(credentialsId: 'note-agent-env', variable: 'ENV_FILE')]) {
          sh 'cp "$ENV_FILE" .env'
        }
        sh 'docker compose $PROD_COMPOSE up -d --build --remove-orphans'
        sh 'sh ci/wait-healthy.sh $PROD_COMPOSE'
      }
      post {
        failure { sh 'docker compose $PROD_COMPOSE logs --tail 100' }
      }
    }
  }

  post {
    always {
      sh 'rm -f .env'
      sh 'docker rmi note-agent:$IMAGE_TAG || true'
    }
  }
}
