FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt -y upgrade
RUN apt-get -y update
RUN apt -y install software-properties-common git vim htop tmux wget

RUN add-apt-repository -y ppa:deadsnakes/ppa
RUN apt -y upgrade
RUN apt-get -y update
RUN apt -y install python3.9 python3-pip python3.9-distutils python3.9-dev
RUN apt update && apt install -y build-essential g++ cmake

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.9 1

WORKDIR /root

RUN pip3 install --upgrade pip
RUN pip3 install --upgrade setuptools
# Chameleon Ubuntu22.04-CUDA version: 12.6
# RUN pip3 install torch==1.8.1+cuXXX -f https://download.pytorch.org/whl/torch_stable.html
RUN pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
RUN pip3 install transformers==4.21.1 scikit-learn==1.1.2 tree-sitter==0.20.0

ENV NVIDIA_VISIBLE_DEVICES all
ENV NVIDIA_DRIVER_CAPABILITIES compute,utility