import os
import json
import shutil
import requests
import argparse
import sys


def load_releases(releases_json_path):
    """Load releases JSON file."""
    if not os.path.exists(releases_json_path):
        raise FileNotFoundError(f"Releases JSON not found at {releases_json_path}")
    with open(releases_json_path, "r") as f:
        try:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Expected JSON to be a dictionary with version keys.")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Error decoding JSON: {e}")


def cleanup_root_directory(root_directory):
    """Remove existing Splunk files in the root directory."""
    for file in os.listdir(root_directory):
        if file.startswith("splunk-") and file.endswith(".tgz"):
            file_path = os.path.join(root_directory, file)
            os.remove(file_path)
            print(f"Deleted old Splunk file: {file_path}")


def ensure_directory_exists(path):
    """Ensure the target directory exists."""
    if not os.path.exists(path):
        os.makedirs(path)
        print(f"Created directory: {path}")


def download_release(url, dest_path):
    """Download a release from the given URL to the specified destination."""
    print(f"Downloading Splunk release from {url} to {dest_path}...")
    response = requests.get(url, stream=True)
    response.raise_for_status()  # Raise an exception for HTTP errors
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(response.raw, f)
    print(f"Download complete: {dest_path}")


def manage_splunk_release(version, releases_json, software_root):
    """Manage Splunk release based on the given version."""
    # Validate the presence of the requested version
    if version not in releases_json:
        raise ValueError(f"Specified version '{version}' not found in releases JSON.")

    release_details = releases_json[version]
    file_name = release_details["file_name"]
    download_url = release_details["download_url"]
    version_directory = os.path.join(software_root, version)
    target_file_path = os.path.join(version_directory, file_name)
    root_file_path = os.path.join(software_root, file_name)

    # Cleanup root directory
    cleanup_root_directory(software_root)

    # Ensure version directory exists
    ensure_directory_exists(version_directory)

    # Check if the file exists; if not, download it
    if not os.path.exists(target_file_path):
        download_release(download_url, target_file_path)
    else:
        print(f"File already exists: {target_file_path}")

    # Copy the file to the root directory
    shutil.copy(target_file_path, root_file_path)
    print(f"Copied {target_file_path} to {root_file_path}")


def return_architecture(version, releases_json):
    """Return the architecture value for the given version."""
    if version not in releases_json:
        raise ValueError(f"Specified version '{version}' not found in releases JSON.")
    return releases_json[version]["architecture"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Splunk release files.")
    parser.add_argument(
        "--version", required=True, help="Splunk release version to process."
    )
    parser.add_argument(
        "--releases_json", required=True, help="Path to Splunk releases JSON file."
    )
    parser.add_argument(
        "--software_root",
        required=False,
        help="Root directory for Splunk software (required for manage mode).",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["manage", "return_architecture"],
        help="Operation mode: manage (default functionality) or return_architecture.",
    )
    args = parser.parse_args()

    try:
        # Load releases
        releases = load_releases(args.releases_json)

        if args.mode == "manage":
            print(f"Loaded releases successfully. Found {len(releases)} versions.")
            if not args.software_root:
                raise ValueError("--software_root is required in manage mode.")
            # Process the specified Splunk version
            manage_splunk_release(args.version, releases, args.software_root)
            print("Splunk release management completed successfully.")
        elif args.mode == "return_architecture":
            # Return the architecture for the specified Splunk version
            architecture = return_architecture(args.version, releases)
            print(architecture)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
