#!/usr/bin/env python3
"""Direct boto3 smoke test for a deployed AgentCore runtime.

Use this to confirm your AgentCore agent works *before* wiring it into
ax-gateway. It calls InvokeAgentRuntime exactly the way the gateway
`bedrock_agentcore` bridge does: payload {"prompt": ...}, qualifier DEFAULT,
and a runtimeSessionId padded to AgentCore's 33-char minimum.

    pip install 'boto3>=1.38'
    python invoke_agent.py "who is on call today?" \
        --arn arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/StandupBot-abc123

Auth comes from the boto3 default credential chain (env vars,
~/.aws/credentials, SSO, instance profile). Pass --profile to pick one.
"""

from __future__ import annotations

import argparse
import json
import uuid

import boto3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Tell me a joke")
    parser.add_argument("--arn", required=True, help="AgentCore runtime ARN (from `agentcore status`)")
    parser.add_argument("--region", default=None, help="AWS region (defaults to ARN / profile region)")
    parser.add_argument("--qualifier", default="DEFAULT", help="Endpoint qualifier (default: DEFAULT)")
    parser.add_argument("--profile", default=None, help="AWS profile name")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    client = session.client("bedrock-agentcore", region_name=args.region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=args.arn,
        runtimeSessionId=uuid.uuid4().hex.ljust(33, "_"),  # AgentCore requires >= 33 chars
        qualifier=args.qualifier,
        payload=json.dumps({"prompt": args.prompt}).encode(),
    )

    content_type = str(response.get("contentType") or "").lower()
    body = response.get("response")

    if "event-stream" in content_type:
        for raw in body.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            print(line)
    else:
        chunks = [c.decode("utf-8") if isinstance(c, bytes) else c for c in body]
        print("".join(chunks))


if __name__ == "__main__":
    main()
