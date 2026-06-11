"""Schema sanity for scenarios/hotel-reservation/deploy/*.yaml.

Manifests are config, not executable code, but they MUST parse and carry the
invariants the rest of the pipeline relies on: an ``app:`` label equal to the
Deployment name (prober + ChaosEngine ``applabel`` convention), explicit
``resources.requests`` on every container (the DESIGN §7.1 capacity budget is
the sum of these), pinned image tags, single-replica defaults, and a
ChaosEngine whose ``appns`` pins the scenario namespace (config/loader.py
derives the provision namespace from it). The Deployment set is also locked to
``topology.json``'s service set so the two cannot drift apart silently.
"""

import json
from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "hotel-reservation" / "deploy"
TOPOLOGY = DEPLOY_DIR.parent / "topology.json"
NAMESPACE = "hotel-reservation"


def load_docs():
    docs = []
    for path in sorted(DEPLOY_DIR.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            assert doc, f"{path.name}: empty YAML document"
            docs.append((path.name, doc))
    return docs


def deployments():
    return [(name, doc) for name, doc in load_docs() if doc["kind"] == "Deployment"]


def test_every_document_has_kind_and_name():
    docs = load_docs()
    assert docs, "no manifests found"
    for filename, doc in docs:
        assert doc.get("kind"), f"{filename}: missing kind"
        assert doc.get("metadata", {}).get("name"), f"{filename}: missing metadata.name"


def test_only_expected_kinds_present():
    kinds = {doc["kind"] for _, doc in load_docs()}
    assert kinds == {"Deployment", "Service", "ChaosEngine"}


def test_deployments_follow_app_label_convention():
    for filename, doc in deployments():
        name = doc["metadata"]["name"]
        where = f"{filename}/{name}"
        assert doc["metadata"]["labels"]["app"] == name, where
        assert doc["spec"]["selector"]["matchLabels"]["app"] == name, where
        assert doc["spec"]["template"]["metadata"]["labels"]["app"] == name, where
        assert doc["spec"].get("replicas") == 1, f"{where}: single-replica default expected"


def test_every_container_has_requests_and_pinned_image():
    for filename, doc in deployments():
        name = doc["metadata"]["name"]
        containers = doc["spec"]["template"]["spec"]["containers"]
        assert containers, f"{filename}/{name}: no containers"
        for container in containers:
            where = f"{filename}/{name}/{container.get('name')}"
            requests = container.get("resources", {}).get("requests", {})
            assert requests.get("cpu"), f"{where}: missing resources.requests.cpu"
            assert requests.get("memory"), f"{where}: missing resources.requests.memory"
            image = container["image"]
            assert ":" in image and not image.endswith(":latest"), f"{where}: unpinned {image}"


def test_services_select_a_deployed_app():
    deployment_names = {doc["metadata"]["name"] for _, doc in deployments()}
    services = [(name, doc) for name, doc in load_docs() if doc["kind"] == "Service"]
    assert services
    for filename, doc in services:
        selector_app = doc["spec"]["selector"]["app"]
        where = f"{filename}/{doc['metadata']['name']}"
        assert selector_app in deployment_names, f"{where}: selector targets nothing"


def test_chaosengine_pins_scenario_namespace():
    engines = [(name, doc) for name, doc in load_docs() if doc["kind"] == "ChaosEngine"]
    assert len(engines) == 1
    _, engine = engines[0]
    appinfo = engine["spec"]["appinfo"]
    assert appinfo["appns"] == NAMESPACE  # config/loader.py derives the namespace from this
    assert appinfo["applabel"] == "app=frontend"
    probe = engine["spec"]["experiments"][0]["spec"]["probe"][0]
    assert f"frontend.{NAMESPACE}.svc.cluster.local:5000" in probe["httpProbe/inputs"]["url"]


def test_deployments_match_topology_services():
    """The manifests and the static solver-gate graph describe the same 19 services."""
    topology = json.loads(TOPOLOGY.read_text())
    deployment_names = {doc["metadata"]["name"] for _, doc in deployments()}
    assert deployment_names == set(topology["services"])
