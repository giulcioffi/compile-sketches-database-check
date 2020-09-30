import csv
import io
import json
import logging
import os
import pathlib
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def main():
    set_verbosity(enable_verbosity=False)

    database_check = DatabaseCheck(repository_name=os.environ["GITHUB_REPOSITORY"],
                                   sketches_reports_source=os.environ["INPUT_SKETCHES-REPORTS-SOURCE"],
                                   token=os.environ["INPUT_GITHUB-TOKEN"])

    database_check.database_check()


    def set_verbosity(enable_verbosity):
    """Turn debug output on or off.

    Keyword arguments:
    enable_verbosity -- this will generally be controlled via the script's --verbose command line argument
                              (True, False)
    """
    # DEBUG: automatically generated output and all higher log level output
    # INFO: manually specified output and all higher log level output
    verbose_logging_level = logging.DEBUG

    if type(enable_verbosity) is not bool:
        raise TypeError
    if enable_verbosity:
        logger.setLevel(level=verbose_logging_level)
    else:
        logger.setLevel(level=logging.WARNING)


class DatabaseCheck:
    """Methods for checking the compilation results against the database

    Keyword arguments:
    repository_name -- repository owner and name e.g., octocat/Hello-World
    artifact_name -- name of the workflow artifact that contains the memory usage data
    token -- GitHub access token
    """
    report_key_beginning = "**Memory usage change @ "

    class ReportKeys:
        """Key names used in the sketches report dictionary"""
        boards = "boards"
        board = "board"
        commit_hash = "commit_hash"
        commit_url = "commit_url"
        sizes = "sizes"
        name = "name"
        absolute = "absolute"
        relative = "relative"
        current = "current"
        previous = "previous"
        delta = "delta"
        minimum = "minimum"
        maximum = "maximum"
        sketches = "sketches"
        compilation_success = "compilation_success"

    def __init__(self, repository_name, sketches_reports_source, token):
        self.repository_name = repository_name
        self.sketches_reports_source = sketches_reports_source
        self.token = token

    def database_check(self):
        #"""Comment a report of memory usage change to pull request(s)."""
        database_report = self.get_database()

        # The sketches reports will be in a local folder location specified by the user
        self.database_check_from_local_reports(database_report=database_report)

    def get_database(self):
        logger.debug("Getting expected compilation results database")
        database_artifact_object = self.get_artifact("https://github.com/giulcioffi/compile-sketches/tree/CheckAgainstDatabase/database")

        database_report = self.get_sketches_reports(artifact_folder_object=database_artifact_object)

        return database_report

    def database_check_from_local_reports(self, database_report):
        """Comment a report of memory usage change to the pull request."""
        sketches_reports_folder = pathlib.Path(os.environ["GITHUB_WORKSPACE"], self.sketches_reports_source)
        sketches_reports = self.get_sketches_reports(artifact_folder_object=sketches_reports_folder)

        if sketches_reports:
            self.check_against_database(sketches_reports=sketches_reports, database_report=database_report)

    def get_artifact(self, artifact_download_url):
        """Download and unzip the artifact and return an object for the temporary directory containing it

        Keyword arguments:
        artifact_download_url -- URL to download the artifact from GitHub
        """
        # Create temporary folder
        artifact_folder_object = tempfile.TemporaryDirectory(prefix="reportsizedeltas-")
        try:
            # Download artifact
            with open(file=artifact_folder_object.name + "/" + self.sketches_reports_source + ".zip",
                      mode="wb") as out_file:
                with self.raw_http_request(url=artifact_download_url) as fp:
                    out_file.write(fp.read())

            # Unzip artifact
            artifact_zip_file = artifact_folder_object.name + "/" + self.sketches_reports_source + ".zip"
            with zipfile.ZipFile(file=artifact_zip_file, mode="r") as zip_ref:
                zip_ref.extractall(path=artifact_folder_object.name)
            os.remove(artifact_zip_file)

            return artifact_folder_object

        except Exception:
            artifact_folder_object.cleanup()
            raise

    def get_sketches_reports(self, artifact_folder_object):
        """Parse the artifact files and return a list containing the data.

        Keyword arguments:
        artifact_folder_object -- object containing the data about the temporary folder that stores the markdown files
        """
        with artifact_folder_object as artifact_folder:
            # artifact_folder will be a string when running in non-local report mode
            artifact_folder = pathlib.Path(artifact_folder)
            sketches_reports = []
            for report_filename in sorted(artifact_folder.iterdir()):
                # Combine sketches reports into an array
                with open(file=report_filename.joinpath(report_filename)) as report_file:
                    report_data = json.load(report_file)
                    if (
                        (self.ReportKeys.boards not in report_data)
                        or (self.ReportKeys.maximum
                            not in report_data[self.ReportKeys.boards][0][self.ReportKeys.sizes][0])
                    ):
                        # Sketches reports use an old format, skip
                        print("Old format sketches report found, skipping")
                        continue

                    for fqbn_data in report_data[self.ReportKeys.boards]:
                        if self.ReportKeys.sizes in fqbn_data:
                            # The report contains deltas data
                            sketches_reports.append(report_data)
                            break

        if not sketches_reports:
            print("No size deltas data found in workflow artifact for this PR. The compile-examples action's "
                  "enable-size-deltas-report input must be set to true to produce size deltas data.")

        return sketches_reports

    def check_against_database(self, sketches_reports, database_report):
        """Return the Markdown for the deltas report comment.

        Keyword arguments:
        sketches_reports -- list of sketches_reports containing the data to generate the deltas report from
        """
        all_compilations_successful = True

        for sketch_report in sketches_reports:
            if sketch_report[self.ReportKeys.compilation_success] == "false":
                name_report = sketch_report[self.ReportKeys.name]
                for sketch_of_database in database_report:
                    if sketch_of_database[self.ReportKeys.name] == name_report:
                        if sketch_of_database[self.ReportKeys.compilation_success] == "true":
                            all_compilations_successful = False
                        break


        if not all_compilations_successful:
            print("::error::One or more compilations failed")
            sys.exit(1)

    def get_json_response(self, url):
        """Load the specified URL and return a dictionary:
        json_data -- JSON object containing the response
        additional_pages -- indicates whether more pages of results remain (True, False)
        page_count -- total number of pages of results

        Keyword arguments:
        url -- the URL to load
        """
        try:
            response_data = self.http_request(url=url)
            try:
                json_data = json.loads(response_data["body"])
            except json.decoder.JSONDecodeError as exception:
                # Output some information on the exception
                logger.warning(str(exception.__class__.__name__) + ": " + str(exception))
                # pass on the exception to the caller
                raise exception

            if not json_data:
                # There was no HTTP error but an empty list was returned (e.g. pulls API request when the repo
                # has no open PRs)
                page_count = 0
                additional_pages = False
            else:
                page_count = get_page_count(link_header=response_data["headers"]["Link"])
                if page_count > 1:
                    additional_pages = True
                else:
                    additional_pages = False

            return {"json_data": json_data, "additional_pages": additional_pages, "page_count": page_count}
        except Exception as exception:
            raise exception

    def http_request(self, url, data=None):
        """Make a request and return a dictionary:
        read -- the response
        info -- headers
        url -- the URL of the resource retrieved

        Keyword arguments:
        url -- the URL to load
        data -- data to pass with the request
                (default value: None)
        """
        with self.raw_http_request(url=url, data=data) as response_object:
            return {"body": response_object.read().decode(encoding="utf-8", errors="ignore"),
                    "headers": response_object.info(),
                    "url": response_object.geturl()}

    def raw_http_request(self, url, data=None):
        """Make a request and return an object containing the response.

        Keyword arguments:
        url -- the URL to load
        data -- data to pass with the request
                (default value: None)
        """
        # Maximum times to retry opening the URL before giving up
        maximum_urlopen_retries = 3

        logger.info("Opening URL: " + url)

        # GitHub recommends using user name as User-Agent (https://developer.github.com/v3/#user-agent-required)
        headers = {"Authorization": "token " + self.token, "User-Agent": self.repository_name.split("/")[0]}
        request = urllib.request.Request(url=url, headers=headers, data=data)

        retry_count = 0
        while retry_count <= maximum_urlopen_retries:
            retry_count += 1
            try:
                # The rate limit API is not subject to rate limiting
                if url.startswith("https://api.github.com") and not url.startswith("https://api.github.com/rate_limit"):
                    self.handle_rate_limiting()
                return urllib.request.urlopen(url=request)
            except Exception as exception:
                if not determine_urlopen_retry(exception=exception):
                    raise exception

        # Maximum retries reached without successfully opening URL
        raise TimeoutError("Maximum number of URL load retries exceeded")

    def handle_rate_limiting(self):
        """Check whether the GitHub API request limit has been reached.
        If so, exit with exit status 0.
        """
        rate_limiting_data = self.get_json_response(url="https://api.github.com/rate_limit")["json_data"]
        # GitHub has two API types, each with their own request limits and counters.
        # "search" applies only to api.github.com/search.
        # "core" applies to all other parts of the API.
        # Since this code only uses the "core" API, only those values are relevant
        logger.debug("GitHub core API request allotment: " + str(rate_limiting_data["resources"]["core"]["limit"]))
        logger.debug("Remaining API requests: " + str(rate_limiting_data["resources"]["core"]["remaining"]))
        logger.debug("API request count reset time: " + str(rate_limiting_data["resources"]["core"]["reset"]))

        if rate_limiting_data["resources"]["core"]["remaining"] == 0:
            # GitHub uses a fixed rate limit window of 60 minutes. The window starts when the API request count goes
            # from 0 to 1. 60 minutes after the start of the window, the request count is reset to 0.
            print("::warning::GitHub API request quota has been reached. Giving up for now.")
            sys.exit(0)
