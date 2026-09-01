export const environment = {
  production: false,
  apiPrefix: 'https://sporting-zombie-kennel.ngrok-free.dev/api/leadai',
  wsUrl: 'wss://sporting-zombie-kennel.ngrok-free.dev',
  authConfig: {
    issuer: 'https://192.168.2.100:7075',
    clientId: 'angular-client',
    loginRedirectUri: 'http://localhost:4300/auth/callback',
    postLogoutRedirectUri: 'http://localhost:4300/',
    pkce: true,
    clientSecret: '',
    checkSessionApi: 'https://192.168.2.100:7075/api/session',
  },
};
