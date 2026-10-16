"""Single source of truth for constructing the Gemini client. Supports
both the AI Studio API-key path and the Vertex AI path -- the latter
exists because an AI Studio key's project turned out to have billing
(Prepay) attached and got charged; Vertex AI billed through a project
with no payment method attached is the structural fix, verified against
the real API before this was written.
"""
import os

from google import genai

from app.config import settings

if settings.use_vertex and settings.vertex_project:
    # google-auth's ADC resolution does its own, separate lookup for a
    # "quota project" and warns ("No project ID could be determined") if
    # it isn't in the environment -- even though we pass `project=`
    # explicitly to genai.Client() below for the actual API calls.
    # Harmless either way, but exporting it here makes the warning go away
    # instead of leaving confusing noise in the logs.
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.vertex_project)


def get_live_client() -> genai.Client:
    """Client for the Live API (audio). On Vertex, confirmed only
    reachable at a specific region, not "global"."""
    if settings.use_vertex:
        return genai.Client(
            vertexai=True, project=settings.vertex_project, location=settings.vertex_location_live
        )
    return genai.Client(api_key=settings.gemini_api_key)


def get_text_client() -> genai.Client:
    """Client for plain (non-Live) generate_content/chat calls. On
    Vertex, confirmed only reachable at location="global" -- the same
    model 404s at "us-central1" even though Live needs that region."""
    if settings.use_vertex:
        return genai.Client(
            vertexai=True, project=settings.vertex_project, location=settings.vertex_location_text
        )
    return genai.Client(api_key=settings.gemini_api_key)
