import requests
import json
import argparse
import sys
import time


def install_apps(
    base_url,
    token,
    env_name,
    splunkbase_username,
    splunkbase_password,
    splunk_username,
    splunk_password,
    apps_file,
    apply_shc_bundle,
    verify_ssl,
    timeout,
    batch,
):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Load apps from JSON file
    with open(apps_file, "r") as file:
        apps = json.load(file)

    if batch:
        # **Batch Install Mode**
        install_url = f"{base_url}/stacks/{env_name}/batch_install_apps"
        payload = {
            "splunkbase_username": splunkbase_username,
            "splunkbase_password": splunkbase_password,
            "splunk_username": splunk_username,
            "splunk_password": splunk_password,
            "apply_shc_bundle": apply_shc_bundle,
            "apps": [
                {
                    "splunkbase_app_id": str(app.get("splunkbaseID", None)),
                    "splunkbase_app_name": app.get("name", None),
                    "version": app.get("version", None),
                }
                for app in apps
            ],
        }

        print(f"🚀 Installing {len(apps)} apps in batch mode...")

        try:
            response = requests.post(
                install_url,
                headers=headers,
                json=payload,
                verify=verify_ssl,
                timeout=timeout,
            )
            response.raise_for_status()
            print(
                f"✅ Batch install successful:\n{json.dumps(response.json(), indent=4)}"
            )
        except requests.exceptions.RequestException as e:
            print(f"❌ Batch install failed: {e}")
            sys.exit(1)

    else:
        # **Single App Install Mode**
        install_url = f"{base_url}/stacks/{env_name}/install_splunk_app"

        for app in apps:
            payload = {
                "splunkbase_username": splunkbase_username,
                "splunkbase_password": splunkbase_password,
                "splunkbase_app_id": str(app.get("splunkbaseID", None)),
                "splunkbase_app_name": app.get("name", None),
                "version": app.get("version", None),
                "splunk_username": splunk_username,
                "splunk_password": splunk_password,
                "apply_shc_bundle": apply_shc_bundle,
            }

            if not all(payload.values()):
                print(
                    f"❌ ERROR: Missing required fields in app definition: {json.dumps(app, indent=4)}"
                )
                sys.exit(1)

            print(
                f"📦 Installing app: {app['splunkbase_app_name']} (ID: {app['splunkbase_app_id']}, Version: {app['version']})..."
            )

            max_duration = 300
            wait_time = 2
            start_time = time.time()

            while True:
                try:
                    response = requests.post(
                        install_url,
                        headers=headers,
                        json=payload,
                        verify=verify_ssl,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    print(
                        f"✅ Installed {app['splunkbase_app_name']}:\n{json.dumps(response.json(), indent=4)}"
                    )
                    break
                except requests.exceptions.RequestException as e:
                    elapsed_time = time.time() - start_time
                    if elapsed_time > max_duration:
                        print(f"❌ Failed to install {app['splunkbase_app_name']}: {e}")
                        sys.exit(1)
                    else:
                        print(f"🔄 Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Install Splunkbase apps via Splunk EAM API."
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
        "--splunkbase_username", required=True, help="Splunkbase username."
    )
    parser.add_argument(
        "--splunkbase_password", required=True, help="Splunkbase password."
    )
    parser.add_argument("--splunk_username", required=True, help="Splunk username.")
    parser.add_argument("--splunk_password", required=True, help="Splunk password.")
    parser.add_argument(
        "--apps_file",
        required=True,
        help="Path to the JSON file containing app definitions.",
    )
    parser.add_argument(
        "--apply_shc_bundle",
        default=False,
        nargs="?",
        const=True,
        help="Apply the bundle automatically if running a Search Head Cluster.",
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
        help="Timeout in seconds for the request (default: 300 seconds).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use batch install mode to install multiple apps in a single API call.",
    )

    args = parser.parse_args()

    # Determine the SSL verification setting
    verify_ssl = args.verify if args.verify not in [True, "True"] else True

    install_apps(
        base_url=args.base_url,
        token=args.token,
        env_name=args.env_name,
        splunkbase_username=args.splunkbase_username,
        splunkbase_password=args.splunkbase_password,
        splunk_username=args.splunk_username,
        splunk_password=args.splunk_password,
        apps_file=args.apps_file,
        apply_shc_bundle=args.apply_shc_bundle,
        verify_ssl=verify_ssl,
        timeout=args.timeout,
        batch=args.batch,
    )
