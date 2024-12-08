# Splunk Enterprise API Manager (Splunk AEM)

## Overview

The Splunk Enterprise API Manager (Splunk AEM) is a docker based API solution designed to manage Splunk Enterprise environments programmatically. 

Splunk AEM provides a RESTful API to manage Splunk Enterprise environments:

- The API is leveraging FastAPI and Uvicorn to provide a high performance and asynchronous API.
- Uses Redis as a cache to store the state of the Splunk Enterprise environments, manage tokens and more.

## Features

**Main features:**

- Conceptualise Splunk Enterprise environments as stacks.
- Create and manage authentication with bearer authentication tokens, which includes rotation, tokens expiration, tokens refresh and more.
- Manage Splunk indexes programmatically to distributed or standalone environments.
- Install and remove Splunk Base applications programmatically, on distributed or standalone environments.
- Ibstall and remove Splunk Private applications programmatically, on distributed or standalone environments.
- Trigger Splunk restarts programmatically, on distributed or standalone environments.
- Trigger Splunk Indexer Cluster rolling restarts programmatically.
- Trigger Splunk Search Head Cluster rolling restarts programmatically.

## Requirements and Installation

### Requirements

- Docker or other container engine compatible with Docker such as Podman
- An SSH private key to access the Splunk Enterprise environments, the user should be able to run sudo commands with no password.
- An Ansible inventory file with the Splunk Enterprise environments to manage. (example provided in this documentation)

### Installation

- The container listens to 8443 port by default.
- Download the Docker image for Splunk EAM fron our repository or DockerHub.
- Create two Docker volumes for the data persistence:

*Splunk AEM data volume:*

- Create a docker volume called ``splunk-aem-data`` to store the data of the Splunk AEM API.

    docker volume create splunk-aem-data

*Splunk AEM config volume:*

- Create a docker volume called ``splunk-aem-config`` to store the configuration files of the Splunk AEM API.

Start the container, example:

    docker run -d --name splunk-aem -p 8443:8443 -v splunk-aem-data:/app/data -v splunk-aem-config:/app/config splunk-aem:latest

Start the container and review the container logs, example of expected results:

    Starting Redis server and logging to /app/logs/redis.log...
    Waiting for Redis to be ready...
    7:C 08 Dec 2024 14:22:52.369 # oO0OoO0OoO0Oo Redis is starting oO0OoO0OoO0Oo
    7:C 08 Dec 2024 14:22:52.369 # Redis version=7.0.15, bits=64, commit=00000000, modified=0, pid=7, just started
    7:C 08 Dec 2024 14:22:52.369 # Configuration loaded
    7:M 08 Dec 2024 14:22:52.369 * monotonic clock: POSIX clock_gettime
    7:M 08 Dec 2024 14:22:52.369 * Running mode=standalone, port=6379.
    7:M 08 Dec 2024 14:22:52.370 # Server initialized
    7:M 08 Dec 2024 14:22:52.370 * Reading RDB base file on AOF loading...
    7:M 08 Dec 2024 14:22:52.370 * Loading RDB produced by version 7.0.15
    7:M 08 Dec 2024 14:22:52.370 * RDB age 3171 seconds
    7:M 08 Dec 2024 14:22:52.370 * RDB memory usage when created 0.84 Mb
    7:M 08 Dec 2024 14:22:52.370 * RDB is base AOF
    7:M 08 Dec 2024 14:22:52.370 * Done loading RDB, keys loaded: 0, keys expired: 0.
    7:M 08 Dec 2024 14:22:52.370 * DB loaded from base file appendonly.aof.1.base.rdb: 0.000 seconds
    7:M 08 Dec 2024 14:22:52.370 * DB loaded from incr file appendonly.aof.1.incr.aof: 0.000 seconds
    7:M 08 Dec 2024 14:22:52.370 * DB loaded from append only file: 0.000 seconds
    7:M 08 Dec 2024 14:22:52.370 * Opening AOF incr file appendonly.aof.1.incr.aof on server start
    7:M 08 Dec 2024 14:22:52.370 * Ready to accept connections
    Redis is ready. Starting application...
    INFO:     Started server process [1]
    INFO:     Waiting for application startup.
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://0.0.0.0:8443 (Press CTRL+C to quit)

The container is ready to be used.

### Update the default admin credentials

Before you can start using the API, you need to update the default admin credentials. The default credentials are:

- login: admin
- password: password

Example: (change localhost to the server name or IP address where the container is running)

    curl -X POST http://localhost:8443/update_password \
    -H "Content-Type: application/json" \
    -d '{
    "current_password": "password",
    "new_password": "ch@ngeMe"
    }'

Response:

    {
        "message": "Admin password updated successfully"
    }

### Create a bearer token

Before you can start using the API, you first need to create a bearer token.

*Run:*

    curl -X POST http://localhost:8443/create_token \
    -H "Content-Type: application/json" \
    -d '{
    "username": "admin",
    "password": "ch@ngeMe"
    }'

Response:

{
  "access_token": "xxxxxxxxxxxxxxxxxxxx",
  "token_type": "bearer"
}

For the purpose of the documentation, we will save and export this token in a shell variable:

    export token='xxxxxxxxxxxxxxxxxxxx'

### Test accessing the API

You can test the API by trying to access to the stacks information, if successful you will get the following response:

Run:

    curl -X GET "http://localhost:8443/stacks" -H "Authorization: Bearer $token"

Response:

    {
    "stacks": {}
    }

Any authentication issue would result in:

    {
    "detail": "Invalid or revoked token. Please authenticate again."
    }

### Configuration and stacks definition

The first step is to add a stack definition, there are two essential use cases:

- Distributed environments, with some variations such as if you are running a Search Head Cluster or not.
- Standalone environments.

**Must be unique and are defined by their stack id.**

#### Distributed example

The following REST call defines a distributed stack:

    curl -H "Authorization: Bearer $token" -X POST http://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-cluster",
        "enterprise_deployment_type": "distributed",
        "shc_cluster": true,
        "cluster_manager_node": "prd1-cl-cm-cm1",
        "shc_deployer_node": "prd1-cl-ds-ds1",
        "shc_members": "prd1-cl-shc-sh1,prd1-cl-shc-sh2,prd1-cl-shc-sh3"
    }'

Response example:

    {
    "message": "Stack 'prd1-cluster' created successfully.",
        "stack": {
            "stack_id": "prd1-cluster",
            "enterprise_deployment_type": "distributed",
            "shc_cluster": true,
            "cluster_manager_node": "prd1-cl-cm-cm1",
            "shc_deployer_node": "prd1-cl-ds-ds1",
            "shc_members": "prd1-cl-shc-sh1,prd1-cl-shc-sh2,prd1-cl-shc-sh3",
            "ansible_python_interpreter": "/usr/bin/python3"
        }
    }

*Alternatively, if this distributed environment does not have an SHC:*

    curl -H "Authorization: Bearer $token" -X POST http://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-cluster",
        "enterprise_deployment_type": "distributed",
        "shc_cluster": false,
        "cluster_manager_node": "prd1-cl-cm-cm1",
    }'

*Accessing the stack definition*:

Run:

    curl -H "Authorization: Bearer $token" -X GET "http://localhost:8443/stacks/prd1-cluster"

#### Standalone example

The following REST call defines a standalone stack:

    curl -H "Authorization: Bearer $token" -X POST http://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-standalone",
        "enterprise_deployment_type": "standalone"
    }'

Response example:

    {
        "message": "Stack 'prd1-standalone' created successfully.",
        "stack": {
            "stack_id": "prd1-standalone",
            "enterprise_deployment_type": "standalone",
            "shc_cluster": null,
            "cluster_manager_node": null,
            "shc_deployer_node": null,
            "shc_members": null,
            "ansible_python_interpreter": "/usr/bin/python3"
        }
    }

*Accessing the stack definition*:

Run:
    
    curl -H "Authorization: Bearer $token" -X GET "http://localhost:8443/stacks/prd1-standalone"

