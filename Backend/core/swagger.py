from fastapi.openapi.utils import get_openapi

def configure_swagger(app, title="Your API Name", version="1.0.0", description="API documentation with Bearer token authentication."):
    """
    Configure Swagger UI for a FastAPI app with Bearer token support.
    """

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=title,
            version=version,
            description=description,
            routes=app.routes,
        )

        # Add Bearer Auth support to Swagger UI
        openapi_schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }

        # Apply BearerAuth globally to all endpoints (optional)
        openapi_schema["security"] = [{"BearerAuth": []}]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    # Override FastAPI's default OpenAPI schema generation
    app.openapi = custom_openapi
