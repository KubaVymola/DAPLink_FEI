FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt -y update && apt install -y build-essential cmake gdb
RUN mkdir /app

WORKDIR /app