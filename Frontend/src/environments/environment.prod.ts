export const environment = {
  production: true,
  apiPrefix: ' https://6b1c-223-178-214-156.ngrok-free.app/api/leadai',
  wsUrl: 'wss://6b1c-223-178-214-156.ngrok-free.app',
  authConfig: {
    issuer: 'https://identity.bharatlogicllp.com',
    clientId: 'angular-client',
    loginRedirectUri:
      'https://tuna-next-internally.ngrok-free.app/auth/callback',
    postLogoutRedirectUri: 'https://tuna-next-internally.ngrok-free.app/',
    pkce: true,
    clientSecret: '',
    checkSessionApi: 'https://identity.bharatlogicllp.com/api/session',
  },
};
