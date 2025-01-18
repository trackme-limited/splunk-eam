import requests
import argparse
import json
import sys


def trigger_operation(
    base_url, token, operation, env_name, payload, verify_ssl, timeout
):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Construct the target URL
    url = f"{base_url}/stacks/{env_name}/{operation}"

    try:
        print(
            f"Triggering operation '{operation}' on environment '{env_name}', sending request to: {url}, timeout: {timeout}, payload: {payload}"
        )
        response = requests.post(
            url, headers=headers, json=payload, verify=verify_ssl, timeout=timeout
        )
        response.raise_for_status()
        try:
            print(
                f"Operation '{operation}' executed successfully.\nResponse:\n{json.dumps(response.json(), indent=4)}"
            )
        except Exception as e:
            print(
                f"Operation '{operation}' executed successfully.\nResponse: {response.json()}"
            )

    except requests.exceptions.Timeout:
        raise Exception(
            f"Operation '{operation}' timed out, consider increasing the timeout."
        )

    except requests.exceptions.RequestException as e:
        raise Exception(f"An error occurred during the operation '{operation}': {e}")


def test_ansible_connection(base_url, token, env_name, verify_ssl, timeout):
    print(f"Testing Ansible connection for stack '{env_name}'...")
    payload = {}  # No additional payload required for this operation
    trigger_operation(
        base_url, token, "ansible_test", env_name, payload, verify_ssl, timeout
    )


def apply_cluster_bundle(
    base_url, token, env_name, username, password, verify_ssl, timeout
):
    print(f"Applying cluster bundle for stack '{env_name}'...")
    payload = {"splunk_username": username, "splunk_password": password}
    trigger_operation(
        base_url, token, "apply_cluster_bundle", env_name, payload, verify_ssl, timeout
    )


def apply_shc_bundle(
    base_url, token, env_name, username, password, verify_ssl, timeout
):
    print(f"Applying SHC bundle for stack '{env_name}'...")
    payload = {"splunk_username": username, "splunk_password": password}
    trigger_operation(
        base_url, token, "apply_shc_bundle", env_name, payload, verify_ssl, timeout
    )


def set_http_max_content_length(
    base_url,
    token,
    env_name,
    username,
    password,
    max_content_length,
    verify_ssl,
    timeout,
):
    print(f"Setting HTTP max content length for SHC in stack '{env_name}'...")
    payload = {
        "splunk_username": username,
        "splunk_password": password,
        "http_max_content_length": max_content_length,
    }
    trigger_operation(
        base_url,
        token,
        "shc_set_http_max_content",
        env_name,
        payload,
        verify_ssl,
        timeout,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trigger various Splunk operations via API."
    )
    parser.add_argument(
        "--base_url", required=True, help="Base URL for the Splunk API."
    )
    parser.add_argument("--token", required=True, help="Authorization token.")
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "restart_splunk",
            "cluster_rolling_restart",
            "shc_rolling_restart",
            "apply_cluster_bundle",
            "apply_shc_bundle",
            "shc_set_http_max_content",
            "ansible_test",
        ],
        help="The Splunk operation to trigger.",
    )
    parser.add_argument(
        "--env_name",
        required=True,
        help="Environment name to be used in the target URL.",
    )
    parser.add_argument(
        "--splunk_username",
        help="Splunk username (required for certain operations).",
        default=None,
    )
    parser.add_argument(
        "--splunk_password",
        help="Splunk password (required for certain operations).",
        default=None,
    )
    parser.add_argument(
        "--limit", help="Limit for the operation (optional).", default=None
    )
    parser.add_argument(
        "--http_max_content_length",
        type=int,
        help="Maximum HTTP content length (required for shc_set_http_max_content).",
        default=None,
    )
    parser.add_argument(
        "--verify",
        default=False,
        nargs="?",
        const=True,
        help="Enable SSL certificate verification. Provide a certificate file path or set to 'True'.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for requests (default: 300 seconds).",
    )

    args = parser.parse_args()

    # Determine the SSL verification setting
    verify_ssl = args.verify if args.verify not in [True, "True"] else True

    # Determine the operation and trigger accordingly
    if args.operation == "ansible_test":
        test_ansible_connection(
            args.base_url,
            args.token,
            args.env_name,
            verify_ssl,
            args.timeout,
        )
    elif args.operation == "apply_cluster_bundle":
        if not args.splunk_username or not args.splunk_password:
            print(
                "Error: 'apply_cluster_bundle' requires splunk_username and splunk_password."
            )
        else:
            apply_cluster_bundle(
                args.base_url,
                args.token,
                args.env_name,
                args.splunk_username,
                args.splunk_password,
                verify_ssl,
                args.timeout,
            )
    elif args.operation == "apply_shc_bundle":
        if not args.splunk_username or not args.splunk_password:
            print(
                "Error: 'apply_shc_bundle' requires splunk_username and splunk_password."
            )
        else:
            apply_shc_bundle(
                args.base_url,
                args.token,
                args.env_name,
                args.splunk_username,
                args.splunk_password,
                verify_ssl,
                args.timeout,
            )
    elif args.operation == "shc_set_http_max_content":
        if (
            not args.splunk_username
            or not args.splunk_password
            or not args.http_max_content_length
        ):
            print(
                "Error: 'shc_set_http_max_content' requires splunk_username, splunk_password, and http_max_content_length."
            )
        else:
            set_http_max_content_length(
                args.base_url,
                args.token,
                args.env_name,
                args.splunk_username,
                args.splunk_password,
                args.http_max_content_length,
                verify_ssl,
                args.timeout,
            )
    else:
        # Generic operation triggering
        payload = {}
        if args.limit:
            payload["limit"] = args.limit
        if args.splunk_username and args.splunk_password:
            payload["splunk_username"] = args.splunk_username
            payload["splunk_password"] = args.splunk_password

        trigger_operation(
            args.base_url,
            args.token,
            args.operation,
            args.env_name,
            payload,
            verify_ssl,
            args.timeout,
        )
