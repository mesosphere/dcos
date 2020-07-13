#!/usr/bin/env groovy

def execute(enterprise_build=false) {
    node('linux') {
      // sh 'docker run --rm -v $PWD:/usr/src -w /usr/src golang:latest go version'
      sh 'echo B – OK'
    }
}

return this
