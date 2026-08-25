export const environment = {
  production: false,
  apiPrefix: 'https://concise-tick-cheerful.ngrok-free.app/api/leadai',
  wsUrl: 'wss://concise-tick-cheerful.ngrok-free.app',
  authConfig: {
    issuer: 'https://192.168.2.100:7075',
    clientId: 'angular-client',
    loginRedirectUri:
      'https://tuna-next-internally.ngrok-free.app/auth/callback',
    postLogoutRedirectUri: 'https://tuna-next-internally.ngrok-free.app/',
    pkce: true,
    clientSecret: '',
    checkSessionApi: 'https://192.168.2.100:7075/api/session',
  },
};
