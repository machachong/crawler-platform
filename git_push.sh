#!/bin/bash
# 自动推送脚本
export GIT_ASKPASS=./git_askpass.py
git push -u origin main
