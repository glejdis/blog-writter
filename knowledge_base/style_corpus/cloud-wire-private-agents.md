# The Cloud Wire — example post: We moved our agents off the public internet (and it was the subnet, again)

Private networking for an Azure AI Foundry agent is mostly straightforward, right
up until the agent can't reach its own model endpoint and you spend an afternoon
staring at a 403. The fix was one subnet delegation. Here's the whole story so you
don't lose the same afternoon.

## The setup

We had a working Foundry agent talking to `gpt-4o` over a public endpoint. Policy
said: no public network access for anything touching customer data. Fine — drop the
account behind a private endpoint, wire up a VNet, done by lunch.

It was not done by lunch.

## What broke

The account went private. The private endpoint resolved. And every agent run died
with `403 Forbidden` reaching the project's own inference endpoint from inside the
VNet. DNS was right. NSG rules were open. The managed identity had
`Cognitive Services OpenAI User`. On paper, nothing was wrong.

The thing nobody mentions in the happy-path docs: the **agent subnet needs a
service delegation**. Without `Microsoft.app/environments` delegation on the
subnet the agent runs in, the platform can't inject the networking it needs, and
the traffic never leaves the way you think it does.

```bicep
resource agentSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = {
  name: 'snet-agent'
  parent: vnet
  properties: {
    addressPrefix: '10.0.1.0/24'
    delegations: [
      {
        name: 'aca-delegation'
        properties: { serviceName: 'Microsoft.app/environments' }
      }
    ]
  }
}
```

Output after redeploying with the delegation in place:

```text
Agent run: status=succeeded  latency=812ms  endpoint=private
```

One block of Bicep. The 403 disappeared.

## Why it works

The delegation hands the subnet to the Container Apps platform that backs the
agent runtime, so it can provision the load balancer and routes the private path
depends on. Skip it and you get a subnet that looks healthy and routes nothing
useful. It's the networking equivalent of a door that's painted on.

## The checklist

Before you blame DNS for a private Foundry agent:

- Is the **agent subnet delegated** to `Microsoft.app/environments`?
- Does the **private endpoint** exist for the account *and* the project?
- Is there a **private DNS zone** linked to the VNet for
  `privatelink.services.ai.azure.com`?
- Does the agent's **managed identity** hold `Cognitive Services OpenAI User` on
  the account?

Four boxes. If the first one's unchecked, the other three won't save you.
