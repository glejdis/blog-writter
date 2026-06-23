# Infra — Part A platform foundation (Bicep)

Runnable Bicep for the platform foundation in
[`../lab-build.md`](../lab-build.md) and the blog post
[`landing-zone-to-secure-ai-part-1.md`](../../../drafts/landing-zone-to-secure-ai-part-1.md).
It deploys **Part A only** — no model is deployed here; that is Part B (the agent).

## What it deploys

| Module | Resources |
| --- | --- |
| `network.bicep` | Simulated hub (`AzureFirewallSubnet`), AI spoke VNet with `snet-app` / `snet-pep` / delegated `snet-agent`, peering, 7 private DNS zones (incl. `services.ai.azure.com`) + links |
| `firewall.bicep` | Azure Firewall + policy with an **FQDN allow-list** (agent egress control) |
| `identity.bicep` | User-assigned managed identity (one per agent) |
| `state.bicep` | Cosmos DB, Storage, AI Search, Key Vault — public access disabled, private endpoints + DNS zone groups |
| `foundry.bicep` | **Microsoft Foundry account** (`kind: AIServices`) + Foundry **project** — agent subnet injected (`networkInjections`), public access disabled, private endpoint |
| `rbac.bicep` | Least-privilege roles: `Cognitive Services OpenAI User`, `Search Index Data Reader`, `Storage Blob Data Reader` |
| `governance.bicep` | Log Analytics workspace + monthly budget with an 80% alert |

## Deploy

```bash
az group create -n rg-ai-lab -l swedencentral
az deployment group create -g rg-ai-lab -f main.bicep -p main.bicepparam
```

Read back the handoff artifacts:

```bash
az deployment group show -g rg-ai-lab -n main \
  --query properties.outputs.managedIdentityPrincipalId.value -o tsv
```

## Options

- **Option A (default):** `deployFirewall = true` builds the simulated hub and
  Azure Firewall in one subscription.
- **Option B:** set `deployFirewall = false` to skip the hub/firewall and peer the
  spoke to your existing landing-zone hub, where egress already lives.

## Before you deploy — verify

These change frequently; confirm current values in Microsoft Learn first:

- The **`snet-agent` delegation** (`agentSubnetDelegation`) required by Foundry
  Agent Service standard setup with private networking.
- **Private DNS zone namespaces** for AI Services / Foundry.
- **Model + region availability** and **API versions**.
- **Foundry RBAC role names** (recently renamed).

> This template encodes the security posture (private only, least privilege,
> controlled egress). A full Foundry Agent Service standard setup also wires the
> account/project **capability hosts** and BYO connections — follow the Agent
> Service quickstart for that step.
