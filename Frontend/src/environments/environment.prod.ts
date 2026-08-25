export const environment = {
  production: true,
  apiPrefix: '/api/leadai',
  wsUrl: '',
  authConfig: {
    issuer: '', // Add production Identity Server URL
    clientId: 'leadai_frontend',
    loginRedirectUri: '/login/callback',
    postLogoutRedirectUri: '/login',
    pkce: true,
    clientSecret: '',
    checkSessionApi: '/api/session'
  }
};
