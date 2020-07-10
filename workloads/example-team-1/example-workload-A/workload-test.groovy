pipeline {
    stages {
        stage('build') {
            steps {
                sh 'docker run --rm -v $PWD:/usr/src -w /usr/src python:3.7 python --version'
            }
        }
    }
}
