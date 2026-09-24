export const environment = {
  production: false,
  apiPrefix: 'http://localhost:5050/api/leadai',
  wsUrl: 'ws://localhost:5050',
  authConfig: {
    issuer: 'https://192.168.2.100:7075',
    clientId: 'angular-client',
    loginRedirectUri: 'http://localhost:4200/auth/callback',
    postLogoutRedirectUri: 'http://localhost:4200/',
    pkce: true,
    clientSecret: '',
    checkSessionApi: 'https://192.168.2.100:7075/api/session',
  },
};
