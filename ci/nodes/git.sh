#!/usr/bin/env bash
set -e

sudo yum -y install https://centos7.iuscommunity.org/ius-release.rpm
sudo yum -y install git2u-all
git --version
