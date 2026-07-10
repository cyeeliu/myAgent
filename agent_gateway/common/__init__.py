"""agent_gateway.common — shared schema, e2a envelope, and helpers.

Mirrors jiuwenswarm's `common/` layout (schema/ + e2a/) so the gateway speaks a
provider-agnostic wire protocol: every channel (web/IM/…) normalizes its
inbound request into an AgentRequest, and agent_core results flow back as
AgentResponse/AgentResponseChunk. agent_core itself is untouched.
"""
