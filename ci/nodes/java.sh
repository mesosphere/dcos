#!/usr/bin/env bash
set -e

sudo yum install -y unzip zip
curl -L "https://get.sdkman.io" -o /tmp/sdkman-installer

# Install SDK Man
bash /tmp/sdkman-installer
source "$HOME/.sdkman/bin/sdkman-init.sh"
sed -i 's/sdkman_auto_answer=false/sdkman_auto_answer=true/' ~/.sdkman/etc/config

# Install Java 8
sdk install java 8.0.242-amzn
sdk use java 8.0.242-amzn
