#!/usr/bin/env groovy

def execute(enterprise_build=false) {
    node('linux') {
      sh 'docker run --rm -v $PWD:/usr/src -w /usr/src python:3.7 python --version'      
    }
}

return this
