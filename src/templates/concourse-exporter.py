#!/usr/bin/env python3
"""
Concourse CI Prometheus Exporter
Exposes per-job build status metrics for Prometheus scraping
Uses fly CLI for authentication
"""

import time
import requests
import logging
import subprocess
import json
from prometheus_client import start_http_server, Gauge, Info

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Metrics
job_last_build_status = Gauge(
    "concourse_job_last_build_status",
    "Last build status of a Concourse job (1=succeeded, 0=failed, -1=errored, -2=aborted, -3=started, -4=pending)",
    ["team", "pipeline", "job"],
)

job_last_build_duration_seconds = Gauge(
    "concourse_job_last_build_duration_seconds",
    "Duration of the last build in seconds",
    ["team", "pipeline", "job", "status"],
)

job_last_build_timestamp = Gauge(
    "concourse_job_last_build_timestamp",
    "Unix timestamp of the last build",
    ["team", "pipeline", "job", "status"],
)

pipeline_count = Gauge("concourse_pipelines_total", "Total number of pipelines")
job_count = Gauge("concourse_jobs_total", "Total number of jobs")

exporter_info = Info("concourse_exporter", "Concourse exporter information")
exporter_info.info({"version": "1.1.0"})


class ConcourseExporter:
    def __init__(
        self,
        concourse_url,
        team="main",
        username="admin",
        password="",
        target_name="exporter",
    ):
        self.concourse_url = concourse_url.rstrip("/")
        self.team = team
        self.username = username
        self.password = password
        self.target_name = target_name
        self.session = requests.Session()

        # Login using fly CLI
        self._fly_login()

    def _fly_login(self):
        """Login to Concourse using fly CLI"""
        try:
            # Login with fly
            cmd = [
                "/usr/local/bin/fly",
                "login",
                "-t",
                self.target_name,
                "-c",
                self.concourse_url,
                "-u",
                self.username,
                "-p",
                self.password,
                "-n",
                self.team,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                raise Exception(f"fly login failed: {result.stderr}")

            logger.info(f"Successfully logged in to Concourse at {self.concourse_url}")
        except Exception as e:
            logger.error(f"Failed to authenticate: {e}")
            raise

    def _fly_curl(self, endpoint):
        """Use fly to make authenticated API requests"""
        try:
            cmd = ["/usr/local/bin/fly", "-t", self.target_name, "curl", endpoint]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                raise Exception(f"fly curl failed: {result.stderr}")

            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"fly curl error for {endpoint}: {e}")
            return None

    def get_pipelines(self):
        """Get all pipelines for the team"""
        result = self._fly_curl(f"/api/v1/teams/{self.team}/pipelines")
        return result if result else []

    def get_jobs(self, pipeline_name):
        """Get all jobs for a pipeline"""
        result = self._fly_curl(
            f"/api/v1/teams/{self.team}/pipelines/{pipeline_name}/jobs"
        )
        return result if result else []

    def collect_metrics(self):
        """Collect metrics from Concourse API"""
        logger.info("Collecting metrics from Concourse...")

        pipelines = self.get_pipelines()
        pipeline_count.set(len(pipelines))

        total_jobs = 0

        for pipeline in pipelines:
            pipeline_name = pipeline["name"]
            logger.debug(f"Processing pipeline: {pipeline_name}")

            jobs = self.get_jobs(pipeline_name)
            total_jobs += len(jobs)

            for job in jobs:
                job_name = job["name"]
                finished_build = job.get("finished_build")
                next_build = job.get("next_build")

                # Determine job status (priority: finished_build > next_build > pending)
                status = None
                status_value = -999

                if finished_build:
                    # Job has completed at least one build
                    status = finished_build.get("status", "unknown")
                    status_value = {
                        "succeeded": 1,
                        "failed": 0,
                        "errored": -1,
                        "aborted": -2,
                    }.get(status, -999)

                    # Set last build status
                    job_last_build_status.labels(
                        team=self.team, pipeline=pipeline_name, job=job_name
                    ).set(status_value)

                    # Set last build duration if available
                    start_time = finished_build.get("start_time")
                    end_time = finished_build.get("end_time")
                    if start_time and end_time:
                        duration = end_time - start_time
                        job_last_build_duration_seconds.labels(
                            team=self.team,
                            pipeline=pipeline_name,
                            job=job_name,
                            status=status,
                        ).set(duration)

                    # Set last build timestamp
                    if end_time:
                        job_last_build_timestamp.labels(
                            team=self.team,
                            pipeline=pipeline_name,
                            job=job_name,
                            status=status,
                        ).set(end_time)

                elif next_build:
                    # Job has a queued or running build
                    status = next_build.get("status", "unknown")
                    status_value = {
                        "started": -3,
                        "pending": -4,
                    }.get(status, -999)

                    job_last_build_status.labels(
                        team=self.team, pipeline=pipeline_name, job=job_name
                    ).set(status_value)

                else:
                    # Job has never been triggered
                    status = "pending"
                    status_value = -4

                    job_last_build_status.labels(
                        team=self.team, pipeline=pipeline_name, job=job_name
                    ).set(status_value)

                logger.debug(f"  Job {job_name}: {status} (value={status_value})")

        job_count.set(total_jobs)
        logger.info(
            f"Collected metrics for {len(pipelines)} pipelines, {total_jobs} jobs"
        )


def main():
    import os
    import sys

    # Configuration from environment variables
    concourse_url = os.getenv("CONCOURSE_URL", "http://localhost:8080")
    team = os.getenv("CONCOURSE_TEAM", "main")
    username = os.getenv("CONCOURSE_USERNAME", "admin")
    password = os.getenv("CONCOURSE_PASSWORD", "")
    port = int(os.getenv("EXPORTER_PORT", "9358"))
    scrape_interval = int(os.getenv("SCRAPE_INTERVAL", "30"))

    logger.info(f"Starting Concourse Exporter v1.1.0")
    logger.info(f"  Concourse URL: {concourse_url}")
    logger.info(f"  Team: {team}")
    logger.info(f"  Username: {username}")
    logger.info(f"  Exporter Port: {port}")
    logger.info(f"  Scrape Interval: {scrape_interval}s")

    if not password:
        logger.error("CONCOURSE_PASSWORD environment variable is required")
        sys.exit(1)

    # Initialize exporter
    try:
        exporter = ConcourseExporter(concourse_url, team, username, password)
    except Exception as e:
        logger.error(f"Failed to initialize exporter: {e}")
        sys.exit(1)

    # Start HTTP server for Prometheus
    start_http_server(port)
    logger.info(f"Exporter listening on port {port}")

    # Scrape loop
    while True:
        try:
            exporter.collect_metrics()
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")

        time.sleep(scrape_interval)


if __name__ == "__main__":
    main()
