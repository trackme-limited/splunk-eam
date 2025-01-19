import requests
import argparse
import json


def destroy_stack(base_url, token, env_name, verify_ssl):
    headers = {"Authorization": f"Bearer {token}"}

    # Define the URL for the DELETE operation
    stack_url = f"{base_url}/stacks/{env_name}"

    try:
        # Delete stack
        print(f"Destroying stack '{env_name}' in Splunk EAM...")
        response = requests.delete(stack_url, headers=headers, verify=verify_ssl)
        response.raise_for_status()
        print(f"Stack '{env_name}' destroyed successfully:")
        print(json.dumps(response.json(), indent=2))

    except requests.exceptions.RequestException as e:
        print(f"An error occurred during the operation: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Destroy a Splunk EAM stack.")
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

    destroy_stack(args.base_url, args.token, args.env_name, verify_ssl)
