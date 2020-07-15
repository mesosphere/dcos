
#!/bin/bash
# set -exuo pipefail

if ! command -v dcos &> /dev/null
then
    echo "dcos could not be found. Please install it and run 'dcos cluster setup' before running this script"
    exit
fi

echo "Create a public-private key pair and save each value into a separate file within the current directory."
dcos security org service-accounts keypair jenkins-private-key.pem jenkins-public-key.pem

echo "Create a service account"
dcos security org service-accounts create -p jenkins-public-key.pem -d "Jenkins service account" jenkins-principal
dcos security org service-accounts show jenkins-principal

echo "About creating a service account secret"
dcos security secrets create-sa-secret --strict jenkins-private-key.pem jenkins-principal jenkins-secret
dcos security secrets create -f ./jenkins-private-key.pem jenkins/private_key
dcos security secrets list /
rm -rf jenkins-private-key.pem

echo "Creating and assigning the permissions"
dcos security org users grant jenkins-principal "dcos:mesos:master:framework:role:*" read
dcos security org users grant jenkins-principal "dcos:mesos:master:framework:role:*" create
dcos security org users grant jenkins-principal "dcos:mesos:master:reservation:role:*" read
dcos security org users grant jenkins-principal "dcos:mesos:master:reservation:role:*" create
dcos security org users grant jenkins-principal "dcos:mesos:master:volume:role:*" read
dcos security org users grant jenkins-principal "dcos:mesos:master:volume:role:*" create

dcos security org users grant jenkins-principal dcos:mesos:master:task:user:nobody create
dcos security org users grant jenkins-principal dcos:mesos:agent:task:user:nobody create

dcos security org users grant jenkins-principal dcos:mesos:master:reservation:principal:jenkins-principal delete
dcos security org users grant jenkins-principal dcos:mesos:master:volume:principal:jenkins-principal delete

echo "Create a config.json file"
cat <<EOF > config.json
{
  "service": {
    "name": "jenkins",
    "user": "nobody",
    "security": {
      "service-account": "jenkins-principal",
      "secret-name": "jenkins/private_key",
      "strict-mode": true
    }
  }
}
EOF

echo "Install Jenkins"
dcos package repo add --index=0 jenkins-aws "https://universe-converter.mesosphere.com/transform?url=https://infinity-artifacts.s3.amazonaws.com/permanent/jenkins/assets/4.0.0-2.204.6-beta9/stub-universe-jenkins.json"
dcos package install --options=config.json jenkins --yes
for i in {1..100}
do
  sleep 1
  echo -n "."
done
echo "."


echo "Create Jenkins project"
curl "$(dcos config show core.dcos_url)/service/jenkins/configuration-as-code/checkNewSource" \
     -k \
     --header "Authorization: token=$(dcos config show core.dcos_acs_token)" \
     --data-raw 'newSource=https://raw.githubusercontent.com/janisz/dcos/workloads_tests/workloads/jenkins-job-config.yaml'

curl "$(dcos config show core.dcos_url)/service/jenkins/configuration-as-code/replace" \
     -k \
     --header "Authorization: token=$(dcos config show core.dcos_acs_token)" \
     --data-raw '_.newSource=https://raw.githubusercontent.com/janisz/dcos/workloads_tests/workloads/jenkins-job-config.yaml&json={"newSource":+"https://raw.githubusercontent.com/janisz/dcos/workloads_tests/workloads/jenkins-job-config.yaml"}&replace=Apply+new+conf'
