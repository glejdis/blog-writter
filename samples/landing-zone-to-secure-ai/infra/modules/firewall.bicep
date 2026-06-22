// Azure Firewall with an FQDN allow-list. This is the agentic egress control:
// every outbound tool call from the agent is forced through here and only
// approved destinations are reachable.

@description('Azure region.')
param location string = resourceGroup().location

@description('Short environment/name suffix, e.g. lab.')
param env string

@description('Tags applied to all resources.')
param tags object = {}

@description('Resource ID of the AzureFirewallSubnet in the hub.')
param firewallSubnetId string

@description('FQDNs the agent is allowed to reach for tool calls. Deny is the default for everything else.')
param allowedFqdns array = [
  'api.github.com'
]

resource pip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-afw-${env}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource fwPolicy 'Microsoft.Network/firewallPolicies@2023-11-01' = {
  name: 'afwp-ai-${env}'
  location: location
  tags: tags
  properties: {
    sku: {
      tier: 'Standard'
    }
  }
}

resource ruleGroup 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2023-11-01' = {
  parent: fwPolicy
  name: 'agent-egress'
  properties: {
    priority: 300
    ruleCollections: [
      {
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        name: 'allow-agent-fqdns'
        priority: 300
        action: {
          type: 'Allow'
        }
        rules: [
          {
            ruleType: 'ApplicationRule'
            name: 'allow-approved-fqdns'
            sourceAddresses: [ '*' ]
            protocols: [
              {
                protocolType: 'Https'
                port: 443
              }
            ]
            targetFqdns: allowedFqdns
          }
        ]
      }
    ]
  }
}

resource firewall 'Microsoft.Network/azureFirewalls@2023-11-01' = {
  name: 'afw-ai-${env}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'AZFW_VNet'
      tier: 'Standard'
    }
    firewallPolicy: {
      id: fwPolicy.id
    }
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: {
            id: firewallSubnetId
          }
          publicIPAddress: {
            id: pip.id
          }
        }
      }
    ]
  }
  dependsOn: [
    ruleGroup
  ]
}

output firewallId string = firewall.id
output firewallPrivateIp string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
