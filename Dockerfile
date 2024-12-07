FROM python:3.12-slim

# Create a non-root user named "deployer"
RUN addgroup --system deployer && adduser --system --ingroup deployer --home /home/deployer deployer

# Install dependencies
RUN apt-get update && apt-get install -y \
    procps iputils-ping net-tools openssh-client redis-server \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the main application file and directories
COPY main.py .
COPY ansible/ /app/ansible/

# Ensure data directory exists at runtime (in case it's empty locally)
RUN mkdir -p /app/data

# Temporary directory for Ansible
RUN mkdir -p /app/data/ansible_tmp

# Create a default configuration file
RUN mkdir -p /app/config
COPY splunk_eam_config.json /app/config/splunk_eam_config.json

# Create a log directory
RUN mkdir -p /app/logs

# Set up Redis directory and configuration
USER root
RUN mkdir -p /var/run/redis && \
    mkdir -p /app/config && \
    chown deployer:deployer /var/run/redis && \
    chown -R deployer:deployer /app
COPY redis.conf /app/config/redis.conf

# Copy the entrypoint script
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Switch to the "deployer" user
USER deployer

# Expose the port for the API
EXPOSE 8443

# Disable SSH strict host key checking for Ansible
ENV ANSIBLE_SSH_ARGS="-o StrictHostKeyChecking=no"

# Command to run Redis and the application
CMD ["/app/entrypoint.sh"]
