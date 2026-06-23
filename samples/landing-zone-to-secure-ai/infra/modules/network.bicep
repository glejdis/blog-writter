// Networking: simulated hub (Option A), AI spoke VNet with app / private-endpoint /
// delegated-agent subnets, peering, and the private DNS zones every AI service needs.

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short environment/name suffix, e.g. lab.')
param env string

@description('Tags applied to all resources.')
param tags object = {}

@description('Address space for the simulated hub VNet (Option A).')
param hubAddressSpace string = '10.0.0.0/16'

@description('Address space for the AI spoke VNet.')
param spokeAddressSpace string = '10.1.0.0/16'

@description('Subnet delegation for the Foundry Agent Service compute subnet. Verify the current value in the Agent Service docs before deploying.')
param agentSubnetDelegation string = 'Microsoft.App/environments'

@description('Create a simulated hub VNet with AzureFirewallSubnet (Option A). Set false to peer to an existing hub (Option B).')
param deployHub bool = true

var hubVnetName = 'vnet-hub-${env}'
var spokeVnetName = 'vnet-ai-spoke-${env}'

resource hub 'Microsoft.Network/virtualNetworks@2023-11-01' = if (deployHub) {
  name: hubVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ hubAddressSpace ]
    }
    subnets: [
      {
        name: 'AzureFirewallSubnet'
        properties: {
          addressPrefix: '10.0.1.0/26'
        }
      }
    ]
  }
}

resource spoke 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: spokeVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [ spokeAddressSpace ]
    }
    subnets: [
      {
        name: 'snet-app'
        properties: {
          addressPrefix: '10.1.1.0/24'
        }
      }
      {
        name: 'snet-pep'
        properties: {
          addressPrefix: '10.1.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-agent'
        properties: {
          addressPrefix: '10.1.3.0/24'
          delegations: [
            {
              name: 'agentDelegation'
              properties: {
                serviceName: agentSubnetDelegation
              }
            }
          ]
        }
      }
    ]
  }
}

resource peerHubToSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-11-01' = if (deployHub) {
  parent: hub
  name: 'hub-to-spoke'
  properties: {
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    remoteVirtualNetwork: {
      id: spoke.id
    }
  }
}

resource peerSpokeToHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2023-11-01' = if (deployHub) {
  parent: spoke
  name: 'spoke-to-hub'
  properties: {
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    remoteVirtualNetwork: {
      id: hub.id
    }
  }
}

var privateDnsZoneNames = [
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
  'privatelink.vaultcore.azure.net'
  'privatelink.services.ai.azure.com'
]

resource dnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zone in privateDnsZoneNames: {
  name: zone
  location: 'global'
  tags: tags
}]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zone, i) in privateDnsZoneNames: {
  parent: dnsZones[i]
  name: 'link-spoke'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: spoke.id
    }
  }
}]

output spokeVnetId string = spoke.id
output appSubnetId string = '${spoke.id}/subnets/snet-app'
output peSubnetId string = '${spoke.id}/subnets/snet-pep'
output agentSubnetId string = '${spoke.id}/subnets/snet-agent'
output firewallSubnetId string = deployHub ? '${hub.id}/subnets/AzureFirewallSubnet' : ''
output dnsZoneOpenAiId string = dnsZones[0].id
output dnsZoneCognitiveId string = dnsZones[1].id
output dnsZoneSearchId string = dnsZones[2].id
output dnsZoneBlobId string = dnsZones[3].id
output dnsZoneCosmosId string = dnsZones[4].id
output dnsZoneVaultId string = dnsZones[5].id
output dnsZoneServicesAiId string = dnsZones[6].id
