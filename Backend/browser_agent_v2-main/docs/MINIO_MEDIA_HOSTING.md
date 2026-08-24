# MinIO media hosting

`POST /direct/posts` uploads each base64 media item once to MinIO, then sends
the resulting direct URL to Facebook and Instagram. The rest of the posting
flow is unchanged.

## Required configuration

```env
MINIO_ENDPOINT=https://your-minio-s3-api.example.com
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=lead-ai
MINIO_PREFIX=posts
MINIO_PUBLIC_BASE_URL=https://media.example.com
MINIO_REGION=us-east-1
MINIO_TLS_VERIFY=true
```

`MINIO_ENDPOINT` is the S3-compatible API address used for uploads. It can be
internal if the application can reach it. `MINIO_PUBLIC_BASE_URL` is different:
it must be an external HTTPS origin that Meta's servers can reach. Meta cannot
fetch objects through a private address such as `192.168.x.x` or through the
MinIO Console.

Objects are stored as `posts/YYYY/MM/<uuid>.<extension>`. The application uses
the path-style URL `<public-base>/<bucket>/<object-key>`.

If the internal S3 endpoint uses a self-signed certificate, ask the MinIO team
for the company CA certificate and install it on the application host. As a
temporary internal-network workaround only, set `MINIO_TLS_VERIFY=false`.

## Required MinIO permissions

The application's service account needs `s3:PutObject` for `lead-ai/posts/*`.
For Meta publishing, `lead-ai/posts/*` also needs anonymous/public read access
through the configured public URL (or an approved long-lived presigned-URL
strategy). Keep the rest of the bucket private.

## Verify before posting

Upload a test object, then open its returned URL in an incognito browser or
from a network outside the company VPN. It must download the media directly,
with HTTPS and without login. Only then will Facebook and Instagram be able to
fetch it.
