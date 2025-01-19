import requests
import argparse
import json
import os


def create_stack(base_url, token, env_name, verify_ssl):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Define URLs for each operation
    stack_url = f"{base_url}/stacks"
    inventory_url = f"{base_url}/stacks/{env_name}/inventory"
    ssh_key_url = f"{base_url}/stacks/{env_name}/ssh_key"
    ansible_test_url = f"{base_url}/stacks/{env_name}/ansible_test"

    try:
        # Create stack
        print("Creating stack in Splunk EAM...")
        stack_definition_file = f"config/splunk_eam/stacks_definition/{env_name}.json"
        with open(stack_definition_file, "r") as file:
            stack_definition = json.load(file)

        response = requests.post(
            stack_url, headers=headers, json=stack_definition, verify=verify_ssl
        )
        response.raise_for_status()
        print("Stack created successfully:")
        print(json.dumps(response.json(), indent=2))

        # Push inventory
        print("Pushing stack inventory in Splunk EAM...")
        inventory_file = "output/inventory.json"
        with open(inventory_file, "r") as file:
            inventory = json.load(file)

        response = requests.post(
            inventory_url, headers=headers, json=inventory, verify=verify_ssl
        )
        response.raise_for_status()
        print("Inventory pushed successfully:")
        print(json.dumps(response.json(), indent=2))

        # Push SSH private key
        print("Pushing stack SSH private in Splunk EAM...")
        ssh_private_key = os.environ.get("SSH_PRIVATE_JSON")
        if not ssh_private_key:
            raise ValueError("SSH_PRIVATE_JSON environment variable is not set.")

        response = requests.post(
            ssh_key_url,
            headers=headers,
            json=json.loads(ssh_private_key),
            verify=verify_ssl,
        )
        response.raise_for_status()
        print("SSH private key pushed successfully:")
        print(json.dumps(response.json(), indent=2))

        # Test Ansible connectivity
        print("Testing Ansible connectivity in Splunk EAM...")
        response = requests.post(ansible_test_url, headers=headers, verify=verify_ssl)
        response.raise_for_status()
        print("Ansible connectivity test successful:")
        print(json.dumps(response.json(), indent=2))

    except requests.exceptions.RequestException as e:
        print(f"An error occurred during the operation: {e}")
    except FileNotFoundError as e:
        print(f"File not found: {e}")
    except ValueError as e:
        print(f"Configuration error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a Splunk EAM stack and configure it."
    )
    parser.add_argument(
        "--base_url", required=True, help="Base URL for the Splunk EAM API."
    )
    parser.add_argument("--token", required=True, help="Authorization token.")
    parser.add_argument(
        "--env_name",
        required=True,
        help="Environment name to be used in the target URL.",
    )
    parser.add_argument(
        "--verify",
        default=False,
        nargs="?",
        const=True,
        help="Enable SSL certificate verification. Provide a certificate file path or set to 'True'.",
    )

    args = parser.parse_args()

    # Determine the SSL verification setting
    verify_ssl = args.verify if args.verify not in [True, "True"] else True

    create_stack(args.base_url, args.token, args.env_name, verify_ssl)
