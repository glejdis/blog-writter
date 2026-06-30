targetScope = 'resourceGroup'

param environmentName string
param location string
param tags object
param resourceSuffix string
param foundryProjectEndpoint string
param bingConnectionId string
param chatModel string
param deepResearchModel string
param sandboxMode string

var serviceName = 'web'
var acrName = replace(toLower('cr${environmentName}${resourceSuffix}'), '-', '')

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${environmentName}-${resourceSuffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${environmentName}-${resourceSuffix}'
  location: location
  tags: tags
}

// AcrPull — lets the container app's identity pull the image.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${environmentName}-${resourceSuffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${environmentName}-${resourceSuffix}'
  location: location
  tags: union(tags, {
    'azd-service-name': serviceName
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        stickySessions: {
          affinity: 'sticky'
        }
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: serviceName
          // Placeholder image; azd replaces it with the built image on deploy.
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            {
              name: 'BLOG_WRITER_PROVIDER'
              value: 'foundry'
            }
            {
              name: 'AZURE_AI_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: uami.properties.clientId
            }
            {
              name: 'BLOG_WRITER_MODEL_ORCHESTRATOR'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_IDEATION'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_INTERNAL_KNOWLEDGE'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_RESEARCH'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_PLANNER'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_POC_BUILDER'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_STYLIST'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_DIAGRAMMER'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_WRITER'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_FACT_CHECKER'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_MODEL_CRITIC'
              value: chatModel
            }
            {
              name: 'BLOG_WRITER_DEEP_RESEARCH'
              value: 'true'
            }
            {
              name: 'AZURE_AI_DEEP_RESEARCH_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'AZURE_AI_DEEP_RESEARCH_MODEL'
              value: deepResearchModel
            }
            {
              name: 'AZURE_AI_DEEP_RESEARCH_AGENT_MODEL'
              value: chatModel
            }
            {
              name: 'AZURE_AI_BING_CONNECTION_ID'
              value: bingConnectionId
            }
            {
              name: 'BLOG_WRITER_SANDBOX'
              value: sandboxMode
            }
            {
              name: 'BLOG_WRITER_UI_HOST'
              value: '0.0.0.0'
            }
            {
              name: 'BLOG_WRITER_UI_PORT'
              value: '8000'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 40
              periodSeconds: 30
              failureThreshold: 5
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 15
              failureThreshold: 6
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

output containerAppName string = containerApp.name
output webUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output identityPrincipalId string = uami.properties.principalId
output identityClientId string = uami.properties.clientId
