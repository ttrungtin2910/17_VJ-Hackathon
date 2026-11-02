from garblo.ai import AIProviderError, AzureLookbookProvider
from garblo.ai_providers.contracts import Analysis, parse_analysis
from garblo.ai_providers.fpt_qwen import FPTQwenAnalyzer
from garblo.ai_providers.openai_direct import OpenAIDirectProvider
from garblo.config import Settings


class LegacyAzureProvider:
    """Chỉ phục vụ job legacy, không được chọn ngầm cho request mới."""

    def __init__(self, settings: Settings, model: str, image_model: str | None):
        self.provider = AzureLookbookProvider(
            settings.model_copy(
                update={
                    "azure_openai_chat_deployment": model,
                    "azure_openai_image_deployment": image_model
                    or settings.azure_openai_image_deployment,
                }
            )
        )

    def analyze(self, image, mime_type, before_call):
        import json

        before_call()
        return Analysis(parse_analysis(json.dumps(self.provider.analyse_garment(image, mime_type))))

    def render_look(self, *, before_call, **kwargs):
        return self.provider.render_look(before_call=before_call, **kwargs)


class ProviderRegistry:
    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve(self, provider: str, model: str, image_model: str | None = None):
        if not model:
            raise AIProviderError("Nhà cung cấp chưa được cấu hình", "provider_not_configured")
        if provider == "openai":
            return OpenAIDirectProvider(self.settings, model, image_model)
        if provider == "fpt":
            return FPTQwenAnalyzer(self.settings, model)
        if provider == "azure_openai":
            return LegacyAzureProvider(self.settings, model, image_model)
        raise AIProviderError("Nhà cung cấp không hỗ trợ tác vụ", "provider_not_supported")
