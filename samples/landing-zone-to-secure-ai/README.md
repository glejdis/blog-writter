# Sample: Landing Zone to Secure AI (Part 1)

Companion build artifacts for the blog post
[`drafts/landing-zone-to-secure-ai-part-1.md`](../../drafts/landing-zone-to-secure-ai-part-1.md).

- [`lab-build.md`](./lab-build.md) — reproducible Azure CLI + Bicep build guide.
  Build the **platform foundation** first, then the **agent** on top. Every
  security control in the post (private endpoints, delegated agent subnet,
  firewall egress rules, least-privilege RBAC, BYO agent-state resources, Content
  Safety) is made concrete here.

> Verify Azure AI product names, model/region availability, private DNS zone
> namespaces, and Foundry RBAC role names in Microsoft Learn before deploying —
> they change frequently.
