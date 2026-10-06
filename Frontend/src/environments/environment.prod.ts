export const environment = {
  production: true,
  apiPrefix: 'https://leadai.bharatlogicllp.com/api/api/leadai',
  wsUrl: 'wss://leadai.bharatlogicllp.com/api/api',
  authConfig: {
    issuer: 'https://identity.bharatlogicllp.com',
    clientId: 'angular-client',
    loginRedirectUri: 'https://leadai.bharatlogicllp.com/auth/callback',
    postLogoutRedirectUri: 'https://leadai.bharatlogicllp.com',
    pkce: true,
    clientSecret: '',
    checkSessionApi: 'https://identity.bharatlogicllp.com/api/session',
  },
};
