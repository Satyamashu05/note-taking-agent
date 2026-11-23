// CI/CD for the note-taking agent.
// Every branch: build the image, lint, and boot the full stack (RabbitMQ, Redis, gateway, workers)
// as a throwaway test project. On main only: redeploy the long-running stack on this Jenkins host.
//
// Jenkins needs: Docker + the Compose plugin (2.24.4+) on the agent, and three "Secret text"
// credentials: `gemini-api-key`, `notion-client-id`, `notion-client-secret`.
// Non-secret settings come from .env.example in the repo.
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
        withCredentials([
          string(credentialsId: 'gemini-api-key',       variable: 'GEMINI_API_KEY'),
          string(credentialsId: 'notion-client-id',     variable: 'NOTION_CLIENT_ID'),
          string(credentialsId: 'notion-client-secret', variable: 'NOTION_CLIENT_SECRET'),
          string(credentialsId: 'groq-api-key',         variable: 'GROQ_API_KEY'),
          string(credentialsId: 'openrouter-api-key',   variable: 'OPENROUTER_API_KEY'),
        ]) {
          sh '''
            grep -vE '^(GEMINI_API_KEY|NOTION_CLIENT_ID|NOTION_CLIENT_SECRET|GROQ_API_KEY|OPENROUTER_API_KEY)=' .env.example > .env
            printf 'GEMINI_API_KEY=%s\\n'       "$GEMINI_API_KEY"       >> .env
            printf 'NOTION_CLIENT_ID=%s\\n'     "$NOTION_CLIENT_ID"     >> .env
            printf 'NOTION_CLIENT_SECRET=%s\\n' "$NOTION_CLIENT_SECRET" >> .env
            printf 'GROQ_API_KEY=%s\\n'         "$GROQ_API_KEY"         >> .env
            printf 'OPENROUTER_API_KEY=%s\\n'   "$OPENROUTER_API_KEY"   >> .env
          '''
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
