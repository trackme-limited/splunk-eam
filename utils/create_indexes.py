import json
import requests
import argparse
import time
import sys


def fetch_stack_metadata(base_url, token, env_name, verify_ssl):
    """
    Fetch the metadata for a given stack.
    """
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base_url}/stacks/{env_name}"
    try:
        print(f"Fetching stack metadata for '{env_name}'...")
        response = requests.get(url, headers=headers, verify=verify_ssl)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching stack metadata: {e}")
        raise


def create_indexes(
    json_file,
    base_url,
    env_name,
    token,
    username,
    password,
    verify_ssl,
    stack_metadata,
    use_batch,
):
    """
    Create indexes in Splunk based on the provided JSON file and stack metadata.
    Supports batch processing if `use_batch` is set to True.
    """
    try:
        # Load JSON data
        with open(json_file, "r") as file:
            indexes = json.load(file)
    except FileNotFoundError:
        print(f"Error: File {json_file} not found.")
        return
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {json_file}.")
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Determine if the stack is distributed
    is_distributed = (
        stack_metadata["metadata"]["enterprise_deployment_type"] == "distributed"
    )
    has_shc_cluster = stack_metadata["metadata"]["shc_cluster"] == "True"

    if use_batch:
        # Batch processing mode
        target_url = f"{base_url}/stacks/{env_name}/batch_indexes"

        # Top-level apply_cluster_bundle and apply_shc_bundle
        payload = {
            "splunk_username": username,
            "splunk_password": password,
            "apply_cluster_bundle": is_distributed,  # Apply bundle for distributed stacks
            "apply_shc_bundle": has_shc_cluster,  # Apply SHC bundle if SHC cluster is present
            "indexes": indexes,  # Only include index-specific fields
        }

        # Send batch request
        try:
            print(f"Sending batch request to {target_url}...")
            print(
                f"********* DEBUG *********\nPayload:\n{json.dumps(payload, indent=4)}"
            )
            response = requests.post(
                target_url, headers=headers, json=payload, verify=verify_ssl
            )
            response.raise_for_status()
            print("Batch request successful, response:")
            print(json.dumps(response.json(), indent=4))
        except Exception as e:
            print(f"Batch request failed: {e}")
            sys.exit(1)

    else:
        # Single index creation mode
        target_url = f"{base_url}/stacks/{env_name}/indexes"

        for index in indexes:
            index["splunk_username"] = username
            index["splunk_password"] = password
            if "datatype" not in index:
                index["datatype"] = "event"

            # Make the POST request
            max_duration = 300
            wait_time = 2
            start_time = time.time()

            while True:
                try:
                    response = requests.post(
                        target_url, headers=headers, json=index, verify=verify_ssl
                    )
                    response.raise_for_status()
                    print(f"Request was successful for : {index['name']}, response:")
                    print(json.dumps(response.json(), indent=4))
                    break
                except Exception as e:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > max_duration:
                        print(f"Failed to create index {index['name']}: {e}")
                        sys.exit(1)
                    else:
                        print(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create Splunk indexes from a JSON file."
    )
    parser.add_argument(
        "--json_file", required=True, help="Path to the indexes definition JSON file."
    )
    parser.add_argument("--token", required=True, help="Authorization token.")
    parser.add_argument(
        "--base_url", required=True, help="Base URL for the Splunk EAM API."
    )
    parser.add_argument("--env_name", required=True, help="Environment name.")
    parser.add_argument("--username", required=True, help="Splunk username.")
    parser.add_argument("--password", required=True, help="Splunk password.")
    parser.add_argument(
        "--verify",
        default=False,
        nargs="?",
        const=True,
        help="Enable SSL certificate verification. Provide a certificate file path or set to 'True'.",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use batch mode to create all indexes in a single API call.",
    )

    args = parser.parse_args()

    # Determine the SSL verification setting
    verify_ssl = args.verify if args.verify not in [True, "True"] else True

    try:
        # Fetch stack metadata
        stack_metadata = fetch_stack_metadata(
            args.base_url, args.token, args.env_name, verify_ssl
        )

        # Create indexes using either batch or single mode
        create_indexes(
            args.json_file,
            args.base_url,
            args.env_name,
            args.token,
            args.username,
            args.password,
            verify_ssl,
            stack_metadata,
            args.batch,  # Determines whether to use batch mode
        )
    except Exception as e:
        print(f"Failed to complete the operation: {e}")
