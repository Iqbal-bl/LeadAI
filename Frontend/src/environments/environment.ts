export const environment = {
  production: false,
  apiPrefix: 'https://concise-tick-cheerful.ngrok-free.app/api/leadai',
  wsUrl: 'wss://concise-tick-cheerful.ngrok-free.app',
  authConfig: {
    // issuer: 'https://192.168.2.100:7075',
    issuer: 'https://identity.bharatlogicllp.com',
    clientId: 'angular-client',
    loginRedirectUri: 'http://localhost:4300/auth/callback',
    postLogoutRedirectUri: 'http://localhost:4300/',
    pkce: true,
    clientSecret: '',
    // checkSessionApi: 'https://192.168.2.100:7075/api/session',
    checkSessionApi: 'https://identity.bharatlogicllp.com/api/session',
  },
};
