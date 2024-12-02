FROM python:3.12-slim

# Create a non-root user named "deployer"
RUN addgroup --system deployer && adduser --system --ingroup deployer deployer

# Install dependencies
RUN apt-get update && apt-get install -y \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the main application file
COPY main.py .

# Copy the data directory
COPY data/ ./data/

# Ensure data directory exists at runtime (in case it's empty locally)
RUN mkdir -p /app/data

# Set permissions for deployer
RUN chown -R deployer:deployer /data

# Switch to the "deployer" user
USER deployer

# Expose the port for the API
EXPOSE 8443

# Disable SSH strict host key checking for Ansible
ENV ANSIBLE_SSH_ARGS="-o StrictHostKeyChecking=no"

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8443"]
