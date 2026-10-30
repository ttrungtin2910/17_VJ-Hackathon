Bạn là Senior Backend Engineer kiêm Agentic AI Architect, phụ trách thiết kế và triển khai backend cho Garblo — AI Personal Stylist.

**Mục tiêu:** Xây dựng hệ thống gợi ý phối đồ cá nhân hóa dựa trên tủ quần áo thực tế của người dùng, context và lịch sử feedback.

**Trước khi triển khai:**

1. Đọc toàn bộ cấu trúc repository, AGENTS.md và các quy ước hiện có.
2. Xác định ngôn ngữ, framework, database, API và AI integrations đang được sử dụng.
3. Áp dụng các skill phù hợp: langgraph, langchain, recsys-pipeline-architect, fastapi-expert và api-design-principles. Không thay thế stack hiện tại nếu chưa có lý do kỹ thuật.
4. Đề xuất kiến trúc tối giản, có khả năng mở rộng.

**Các chức năng bắt buộc:**

- Wardrobe Management: lưu trữ và truy xuất các món đồ với category, màu sắc, chất liệu, style, ảnh và metadata.
- Context Understanding: phân tích sự kiện, địa điểm, thời tiết và sở thích.
- Outfit Generation: tạo outfit từ những món đồ người dùng thực sự sở hữu.
- Recommendation Ranking: lọc, chấm điểm và chọn Top 3 outfit dựa trên độ phù hợp.
- Conversational Editing: hỗ trợ chỉnh sửa riêng từng thành phần outfit.
- Feedback & Personalization: sử dụng lựa chọn và đánh giá của người dùng để cải thiện đề xuất về sau.

**Ràng buộc kỹ thuật:**

- Không đề xuất quần áo không tồn tại trong wardrobe trừ khi được yêu cầu mua sắm.
- Sử dụng structured output với item IDs và validation.
- Ưu tiên deterministic logic cho filtering, ranking và tính hợp lệ.
- Dùng LLM khi cần hiểu ý định, lập kế hoạch hoặc giải thích.
- Thiết kế timeout, retries, tracing và giới hạn chi phí LLM.
- Bảo đảm mỗi người dùng chỉ truy cập dữ liệu wardrobe của chính mình.
- Có unit tests, integration tests và các test case đánh giá chất lượng outfit.

**Deliverables:**

1. Báo cáo hiện trạng repository và khoảng cách so với yêu cầu.
2. Architecture Decision Record (ADR).
3. Sơ đồ workflow agent.
4. Database schema và API contracts.
5. Implementation plan chia theo milestone.
6. MVP chạy được, kèm test và hướng dẫn vận hành.

Không triển khai ngay kiến trúc multi-agent phức tạp nếu một orchestrator với các service/tool chuyên trách có thể giải quyết bài toán hiệu quả hơn.