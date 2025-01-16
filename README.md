# Splunk Enterprise API Manager by TrackMe Limited (Splunk AEM)

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
- Install and remove Splunk Private applications programmatically, on distributed or standalone environments.
- Trigger Splunk restarts programmatically, on distributed or standalone environments.
- Trigger Splunk Indexer Cluster rolling restarts programmatically.
- Trigger Splunk Search Head Cluster rolling restarts programmatically.
- Flexibility, listening port and SSL settings can be customised easily, you can also create your own endpoints and your own Ansible playbooks.
- Secured by default, the API is SSL based as a default, and requires default credentials to be updated before endpoints can be used.

## License

**Splunk EAM is governed by Apache 2.0, see:**

- <https://www.apache.org/licenses/LICENSE-2.0>

## Requirements and Installation

### Requirements

- Docker or other container engine compatible with Docker such as Podman
- An SSH private key to access the Splunk Enterprise environments, the user should be able to run sudo commands with no password.
- An Ansible inventory file with the Splunk Enterprise environments to manage. (example provided in this documentation)

### Pull the Docker image

**You can find the release of Splunk EAM in DockerHub:**

- [https://hub.docker.com/repository/docker/trackmelimited/splunk-eam/general](https://hub.docker.com/repository/docker/trackmelimited/splunk-eam/general)

**Pull the latest release in Docker in one command:**

```shell
    docker pull trackmelimited/splunk-eam:latest
```

### Installation

- The container listens to 8443 port by default.
- Download the Docker image for Splunk EAM from our repository or DockerHub.
- Create two Docker volumes for the data persistence:

*Splunk AEM data volume:*

- Create a docker volume called ``splunk-aem-data`` to store the data of the Splunk AEM API.

```shell
    docker volume create splunk-aem-data
```

*Splunk AEM config volume:*

- Create a docker volume called ``splunk-aem-config`` to store the configuration files of the Splunk AEM API.

```shell
    docker volume create splunk-aem-config
```

Start the container, example:

```shell
    docker run -d --name splunk-aem -p 8443:8443 -v splunk-aem-data:/app/data -v splunk-aem-config:/app/config splunk-aem:latest
```

Start the container and review the container logs, example of expected results:

```shell
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
    INFO:     Uvicorn running on https://0.0.0.0:8443 (Press CTRL+C to quit)
```

The container is ready to be used.

### Splunk EAM configuration

You can customise various aspects of Splunk EAM by modifying the generated configuration file, this file is generated first when the container started, meant to be stored in a persistent volume, you can therefore modify it up to your needs:

```shell
    /app/config/gunicorn.conf.py
```

The default configuration looks like the following:

```shell
    #
    # gunicorn.conf.py
    #

    # Bind to all interfaces on port 8443
    bind = "0.0.0.0:8443"

    # Worker configuration
    workers = 8  # Adjust based on `2-4 * (CPU cores)`
    worker_class = "uvicorn.workers.UvicornWorker"
    threads = 4  # Threads per worker for concurrency

    # Timeout settings for long-running tasks
    timeout = 1800  # Allow up to 30 minutes for Ansible tasks
    graceful_timeout = 1800  # Clean worker shutdown

    # Max requests for stability
    max_requests = 1000
    max_requests_jitter = 100

    # SSL configuration
    certfile = "/app/certs/ssl_cert.pem"
    keyfile = "/app/certs/ssl_key.pem"

    # Logging
    accesslog = "/app/logs/gunicorn_access.log"
    errorlog = "/app/logs/gunicorn_error.log"
    loglevel = "info"
```

You can for instance change the listening port, use your own certificates, manage timeouts values, etc.

#### SSL Configuration

The API uses HTTPS for secure communication. It can either automatically generate a self-signed SSL certificate or use a custom certificate provided by the user.

- **Default Behavior:** A self-signed SSL certificate is automatically generated and used if no custom certificate is provided.

- **Custom Certificates:** To use your own certificates, provide the following environment variables when running the container:

  - `USE_EXTERNAL_CERT=true`
  - `EXTERNAL_CERT_FILE=/path/to/your/certificate.pem`
  - `EXTERNAL_KEY_FILE=/path/to/your/private.key`

**Examples:**

- Using the default self-signed certificate:

```shell
    docker run -p 8443:8443 your_image_name
```

- Using custom certificates:

```shell
    docker run \
    -e USE_EXTERNAL_CERT=true \
    -e EXTERNAL_CERT_FILE=/app/certs/custom_cert.pem \
    -e EXTERNAL_KEY_FILE=/app/certs/custom_key.pem \
    -v /local/certs:/app/certs \
    -p 8443:8443 your_image_name
```

Here, /local/certs is a local directory containing the custom_cert.pem and custom_key.pem files, which are mounted to the container’s /app/certs directory.

#### Environment Variable Summary

You could add a quick summary table for all environment variables for easy reference:

| Variable                | Description                                        | Default Value |
|-------------------------|----------------------------------------------------|---------------|
| `USE_EXTERNAL_CERT`      | Enable to use custom SSL certificates.            | `false`       |
| `EXTERNAL_CERT_FILE`     | Path to the custom SSL certificate file.          | Empty         |
| `EXTERNAL_KEY_FILE`      | Path to the custom SSL key file.                  | Empty         |

### Upgrading

To upgrade Splunk EAM, you only need to ensure that you have pulled or refreshed your local Docker registry, then restart your container.

### Hosting the Splunk EAM API behind a reverse proxy

If the API is hosted behind a reverse proxy, such as Nginx, ensure that you allow for suffiscient timeout values.

Indeed, some operations such as applying the Search Head Cluster bundle can require a certain amount time to be completed.

The following shows a functional configuration for Nginx:

```shell
    server {
        server_name splunk-eam.mydomain.com;

        root /var/www/html;
        index index.html index.htm index.nginx-debian.html;

        # Proxy requests to Splunk AEM
        location / {
            proxy_pass https://127.0.0.1:8443; # internal port
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # Allow larger payloads
            client_max_body_size 1000M;

            # Extend timeouts for long-running requests
            proxy_connect_timeout 1800s;  # Time to establish a connection to the backend
            proxy_read_timeout 1800s;     # Time to wait for the backend response
            proxy_send_timeout 1800s;     # Time to send the request to the backend
            send_timeout 1800s;           # Time to send response to the client

        }

        listen 443 ssl;
        ssl_certificate /etc/letsencrypt/live/splunk-eam.mydomain.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/splunk-eam.mydomain.com/privkey.pem;
        include /etc/letsencrypt/options-ssl-nginx.conf;
        ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    }

    server {
        if ($host = splunk-eam.mydomain.com) {
            return 301 https://$host$request_uri;
        }

        listen 80;
        server_name splunk-eam.mydomain.com;
        return 404;
    }
```

### Update the default admin credentials

**Before** you can start using the API, you need to update the default admin credentials. The default credentials are:

- login: admin
- password: password

Example: (change localhost to the server name or IP address where the container is running)

```shell
    curl -k -X POST https://localhost:8443/update_password \
    -H "Content-Type: application/json" \
    -d '{
    "current_password": "password",
    "new_password": "ch@ngeMe"
    }'
```

Response:

```json
    {
        "message": "Admin password updated successfully"
    }
```

### Create a bearer token

Before you can start using the API, you first need to create a bearer token.

*Run:*

```shell
    curl -k -X POST https://localhost:8443/create_token \
    -H "Content-Type: application/json" \
    -d '{
    "username": "admin",
    "password": "ch@ngeMe"
    }'
```

Response:

```json
    {
    "access_token": "xxxxxxxxxxxxxxxxxxxx",
    "token_type": "bearer"
    }
```

For the purpose of the documentation, we will save and export this token in a shell variable:

```shell
    export token='xxxxxxxxxxxxxxxxxxxx'
```

### Test accessing the API

You can test the API by trying to access to the stacks information, if successful you will get the following response:

Run:

```shell
    curl -k -X GET "https://localhost:8443/stacks" -H "Authorization: Bearer $token"
```

Response:

```json
    {
    "stacks": {}
    }
```

Any authentication issue would result in:

```json
    {
    "detail": "Invalid or revoked token. Please authenticate again."
    }
```

### Configuration and stacks definition

The first step is to add a stack definition, there are two essential use cases:

- Distributed environments, with some variations such as if you are running a Search Head Cluster or not.
- Standalone environments.

**Must be unique and are defined by their stack id.**

#### Distributed example

The following REST call defines a distributed stack:

```shell
    curl -k -H "Authorization: Bearer $token" -X POST https://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-cluster",
        "enterprise_deployment_type": "distributed",
        "shc_cluster": true,
        "cluster_manager_node": "prd1-cl-cm-cm1",
        "shc_deployer_node": "prd1-cl-ds-ds1",
        "shc_members": "prd1-cl-shc-sh1,prd1-cl-shc-sh2,prd1-cl-shc-sh3"
    }'
```

Response example:

```json
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
```

*Alternatively, if this distributed environment does not have an SHC:*

```shell
    curl -k -H "Authorization: Bearer $token" -X POST https://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-cluster",
        "enterprise_deployment_type": "distributed",
        "shc_cluster": false,
        "cluster_manager_node": "prd1-cl-cm-cm1",
    }'
```

*Accessing the stack definition*:

Run:

```shell
    curl -k -H "Authorization: Bearer $token" -X GET "https://localhost:8443/stacks/prd1-cluster"
```

#### Standalone example

The following REST call defines a standalone stack:

```shell
    curl -k -H "Authorization: Bearer $token" -X POST https://localhost:8443/stacks -H "Content-Type: application/json" -d '{
        "stack_id": "prd1-standalone",
        "enterprise_deployment_type": "standalone"
    }'
```

Response example:

```json
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
```

*Accessing the stack definition*:

Run:

```shell
    curl -k -H "Authorization: Bearer $token" -X GET "https://localhost:8443/stacks/prd1-standalone"
```

### Defining and pushing the Ansible inventory

The following shows the inventory example for the distributed environment:

```shell
    [all]
    prd1-cl-cm-cm1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-mc-mc1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-ds-ds1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-ds-lm1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-shc-sh1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-shc-sh2 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-shc-sh3 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-idx-dc1-idx1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-idx-dc1-idx2 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-idx-dc2-idx1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-idx-dc2-idx2 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-hf-dc1-hf1 ansible_host=192.168.1.xx ansible_user=deployer
    prd1-cl-hf-dc1-hf2 ansible_host=192.168.1.xx ansible_user=deployer
```

The following shows the inventory example for the standalone environment:

```shell
    [all]
    prd1-standalone ansible_host=192.168.1.xx ansible_user=deployer
```

Before pushing the inventory to the API (which stores it in Redis), you need to convent it into a JSON format.

You can use the provided Python utility:

- Copy your inventory to inventory.ini in the utils directory.
- Cd unto the utils directory.
- Run:

    Python3 utils/inventory_to_json.py

The inventory.json file will be created in the utils directory.

**Now, push the inventory to stack endpoint:**

*Example for our distributed environment:*

```shell
    curl -k -H "Authorization: Bearer $token" -X POST "https://localhost:8443/stacks/prd1-cluster/inventory" \
    -H "Content-Type: application/json" \
    -d @inventory.json
```

*Example for our standalone environment:*

```shell
    curl -k -H "Authorization: Bearer $token" -X POST "https://localhost:8443/stacks/prd1-standalone/inventory" \
    -H "Content-Type: application/json" \
    -d @inventory.json
```

### Pushing the SSH private key

First, you need to convert your SSH private key to base64 format:

```shell
    cat ~/.ssh/id_rsa | base64 -w 0 > private_key_base64.txt
```

Then, take the content of the private_key_base64.txt file, add it to a JSON file as: (called here private_key.json)

```json
    {"ssh_key_b64": "xxxxxxx"}
```

Finally, push the SSH private key to the stack endpoint:

*Example for our distributed environment

```shell
    curl -k -H "Authorization: Bearer $token" -X POST "https://localhost:8443/stacks/prd1-cluster/ssh_key" \
    -H "Content-Type: application/json" \
    --data-binary @private_key.json
```

### Test Ansible connectivity

You can test the Ansible connectivity to the stack by running the following command:

*Example for our distributed environment*

```shell
    curl -k -H "Authorization: Bearer $token" -X POST "https://localhost:8443/stacks/prd1-cluster/ansible_test"
```

*Response:*

```json
    {
        "message": "Ansible ping test successful",
        "results": [
            {
                "host": "prd1-cl-ds-lm1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-ds-ds1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-mc-mc1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-shc-sh1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-cm-cm1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-shc-sh2",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-shc-sh3",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-idx-dc1-idx1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-idx-dc1-idx2",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-idx-dc2-idx1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-hf-dc1-hf1",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-idx-dc2-idx2",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            },
            {
                "host": "prd1-cl-hf-dc1-hf2",
                "details": {
                    "raw_output": "    \"ansible_facts\": {\n        \"discovered_interpreter_python\": \"/usr/bin/python3.10\"\n    },\n    \"changed\": false,\n    \"ping\": \"pong\"\n}\n"
                }
            }
        ]
    }
```

### Creating custom endpoints calling custom Ansible playbooks

You can optionally extend the API with your own custom endpoints and using Ansible playbooks of yours.

#### Mount bind volumes to /app/custom/bin and /app/custom/ansible

First, you would make custom volumes available to the Docker container.

Example, in the machine hosting Docker, say we have:

```shell
    /opt/splunk-eam/bin
    /opt/splunk-eam/ansible
```

The bin directory will be mounted in the container in /app/custom/bin.
The ansible directory will be mounted in the container in /app/custom/ansible.

**You would make these directories available to the docker container:**

```shell
    docker run -v /opt/splunk-eam/bin:/app/custom/bin \
            -v /opt/splunk-eam/ansible:/app/custom/ansible \
            -p 8443:8443 splunk-eam:latest
```

You can then create your own endpoint using FastApi routing capabilities, the following is an example of a Python code that can be used to create custom endpoints:

```python
    from fastapi import APIRouter

    def register_routes(app):
        router = APIRouter()

        @router.post("/custom_endpoint")
        def custom_playbook_endpoint(stack_id: str):
            """
            Example endpoint to run a custom Ansible playbook.
            """
            playbook_name = "my_custom_playbook.yml"
            ansible_vars = {"stack_id": stack_id}

            # Path to custom playbooks
            playbook_path = f"/app/custom/ansible/{playbook_name}"

            if not pathlib.Path(playbook_path).exists():
                raise HTTPException(status_code=404, detail=f"Playbook {playbook_name} not found.")

            # Example call to run_ansible_playbook
            run_ansible_playbook(stack_id, playbook_path, ansible_vars)

            return {"message": f"Custom playbook {playbook_name} executed successfully."}

        app.include_router(router)
```

Handle both the Python file(s) and Ansible playbook(s) and restart the Docker container, the endpoints should be now available and ready to be used.

### Using the API

#### GET /docs/endpoints

**The API is self-documented and you can access the Swagger UI by visiting the following URL:**

```shell
    curl -k -X GET "https://localhost:8443/docs/endpoints" -H "Authorization: Bearer $token"
```

#### POST /create_token

Create a bearer token for authentication.

```shell
    curl -k -X POST https://localhost:8443/create_token \
    -H "Content-Type: application/json" \
    -d '{
    "username": "admin",
    "password": "your_password"
    }'
```

#### POST /delete_token

Revoke an existing bearer token.

```shell
    curl -k -X POST https://localhost:8443/delete_token \
    -H "Content-Type: application/json" \
    -d '{
    "token": "your_token"
    }'
```

#### POST /update_password

Update the admin password.

```shell
    curl -k -X POST https://localhost:8443/update_password \
    -H "Content-Type: application/json" \
    -d '{
    "current_password": "current_password",
    "new_password": "new_password"
    }'
```

#### POST /stacks

Create a new stack.

```shell
    curl -k -X POST https://localhost:8443/stacks \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "stack_id": "stack_001",
    "enterprise_deployment_type": "standalone",
    "shc_cluster": false
    }'
```

#### GET /stacks

Retrieve all stacks.

```shell
    curl -k -X GET https://localhost:8443/stacks \
    -H "Authorization: Bearer $token"
```

#### GET /stacks/{stack_id}

Retrieve details of a specific stack.

```shell
    curl -k -X GET https://localhost:8443/stacks/stack_001 \
    -H "Authorization: Bearer $token"
```

#### DELETE /stacks/{stack_id}

Delete a specific stack.

```shell
    curl -k -X DELETE https://localhost:8443/stacks/stack_001 \
    -H "Authorization: Bearer $token"
```

#### POST /stacks/{stack_id}/inventory

Upload the Ansible inventory for a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/inventory \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d @inventory.json
```

#### GET /stacks/{stack_id}/inventory

Retrieve the Ansible inventory for a stack.

```shell
    curl -k -X GET https://localhost:8443/stacks/stack_001/inventory \
    -H "Authorization: Bearer $token"
```

#### POST /stacks/{stack_id}/ssh_key

Upload the SSH private key for a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/ssh_key \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "ssh_key_b64": "base64_encoded_ssh_key"
    }'
```

#### POST /stacks/{stack_id}/indexes

Add a new index to a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/indexes \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password",
    "name": "new_index",
    "maxDataSizeMB": 500000,
    "datatype": "event"
    }'
```

#### GET /stacks/{stack_id}/indexes

Retrieve all indexes for a stack.

```shell
    curl -k -X GET https://localhost:8443/stacks/stack_001/indexes \
    -H "Authorization: Bearer $token"
```

#### DELETE /stacks/{stack_id}/indexes/{index_name}

Delete an index from a stack.

```shell
    curl -k -X DELETE https://localhost:8443/stacks/stack_001/indexes/index_name \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/install_splunk_app

Install a Splunk app from Splunkbase on a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/install_splunk_app \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password",
    "splunkbase_username": "splunkbase_user",
    "splunkbase_password": "splunkbase_password",
    "splunkbase_app_id": "app_id",
    "splunkbase_app_name": "app_name",
    "version": "app_version"
    }'
```

#### DELETE /stacks/{stack_id}/delete_splunk_app

Delete a Splunk app from a stack.

```shell
    curl -k -X DELETE https://localhost:8443/stacks/stack_001/delete_splunk_app \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunkbase_app_name": "app_name",
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/install_private_app

Install a private Splunk app on a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/install_private_app \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "app_base64": "base64_encoded_app_tarball",
    "app_name": "private_app_name",
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### DELETE /stacks/{stack_id}/delete_private_app

Delete a private Splunk app from a stack.

```shell
    curl -k -X DELETE https://localhost:8443/stacks/stack_001/delete_private_app \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "app_name": "private_app_name",
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/restart_splunk

Restart Splunk services on a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/restart_splunk \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "limit": "optional_limit_parameter"
    }'
```

#### POST /stacks/{stack_id}/cluster_rolling_restart

Trigger a rolling restart of an indexer cluster.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/cluster_rolling_restart \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/shc_rolling_restart

Trigger a rolling restart of a Search Head Cluster (SHC).

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/shc_rolling_restart \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/ansible_test

Test the Ansible connection to the hosts in a stack.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/ansible_test \
    -H "Authorization: Bearer $token"
```

#### POST /stacks/{stack_id}/apply_cluster_bundle

Apply a cluster bundle on a distributed cluster manager.

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/apply_cluster_bundle \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/apply_shc_bundle

Apply an SHC bundle on a deployer node for a Search Head Cluster (SHC).

```shell
    curl -k -X POST https://localhost:8443/stacks/stack_001/apply_shc_bundle \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{
    "splunk_username": "admin",
    "splunk_password": "password"
    }'
```

#### POST /stacks/{stack_id}/shc_set_http_max_content

Set the HTTP Max Content Length for SHC members in server.conf. This action applies only to distributed stacks with SHC enabled.

```shell
curl -k -X POST https://localhost:8443/stacks/stack_001/shc_set_http_max_content \
-H "Authorization: Bearer $token" \
-H "Content-Type: application/json" \
-d '{
    "splunk_username": "admin",
    "splunk_password": "password",
    "http_max_content_length": 5000000000
}'
```
