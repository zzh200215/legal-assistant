/**
 * 本文件由 scripts/gen-api-types.mjs 自动生成，禁止手工编辑。
 * 数据源：docs/openapi-snapshot.json（后端 OpenAPI 快照）。
 * 用途：为 JS 业务代码提供请求/响应结构契约（JSDoc @typedef），
 *       页面 view model 应基于生成类型派生，不要反改生成文件。
 * @typedef {Object} APIKeyCreate
 * @property {number | null}= expires_days
 * @property {string} name
 * @typedef {Object} ActivateVersionRequest
 * @property {number} version_id
 * @typedef {Object} AgentApprovalDecisionRequest
 * @property {boolean} approved
 * @property {string | null}= decision_note
 * @typedef {Object} AgentApprovalRequestOut
 * @property {number | null}= agent_run_id
 * @property {string | null}= agent_type
 * @property {string} approval_token
 * @property {string} created_at
 * @property {string | null}= decided_at
 * @property {string | null}= decision_note
 * @property {number} id
 * @property {string | null}= input_params
 * @property {string} risk_level
 * @property {string} status
 * @property {string} tool_name
 * @property {number} user_id
 * @typedef {Object} AgentApprovalResumeRequest
 * @property {string | null}= decision_note
 * @typedef {Object} AgentPlanPreviewRequest
 * @property {string} goal
 * @property {number}= max_steps
 * @typedef {Object} AgentPlanPreviewResponse
 * @property {boolean}= can_execute
 * @property {number} estimated_steps
 * @property {Array<string>}= risks
 * @property {Object | null}= selected_skill
 * @property {Array<AgentPlanPreviewStep>}= steps
 * @property {string} summary
 * @typedef {Object} AgentPlanPreviewStep
 * @property {Object}= action_input_preview
 * @property {string} purpose
 * @property {number} step
 * @property {string} tool_name
 * @typedef {Object} AgentRunCancelRequest
 * @property {string | null}= reason
 * @typedef {Object} AgentRunDetailOut
 * @property {Object}= artifacts
 * @property {string | null}= completed_at
 * @property {string} created_at
 * @property {string | null}= error
 * @property {string | null}= failure_reason
 * @property {string | null}= final_answer
 * @property {string} goal
 * @property {number} id
 * @property {string | null}= last_observation
 * @property {Array<ToolCallLogOut>}= logs
 * @property {string | null}= result
 * @property {number | null}= session_id
 * @property {string} status
 * @property {Object}= supervisor_plan
 * @property {number | null}= total_steps
 * @property {number} user_id
 * @typedef {Object} AgentRunRequest
 * @property {string} goal
 * @property {number}= max_steps
 * @property {number | null}= session_id
 * @typedef {Object} AgentRunResponse
 * @property {Object}= artifacts
 * @property {string | null}= error
 * @property {string | null}= failure_reason
 * @property {string | null}= final_answer
 * @property {Array<ToolCallLogOut>}= logs
 * @property {string | null}= result
 * @property {number} run_id
 * @property {string} status
 * @property {Object}= supervisor_plan
 * @typedef {Object} AnalyzeRequest
 * @property {boolean}= async_mode
 * @property {number}= max_length
 * @typedef {Object} AppCreate
 * @property {string | null}= ip_whitelist_json
 * @property {string} name
 * @property {string | null}= webhook_secret
 * @property {string | null}= webhook_url
 * @typedef {Object} ApprovalActionIn
 * @property {string} action
 * @property {string | null}= note
 * @typedef {Object} ApproverIn
 * @property {string | null}= role
 * @property {number} user_id
 * @typedef {Object} AskRequest
 * @property {string} question
 * @typedef {Object} AuditLogOut
 * @property {string} action
 * @property {string} created_at
 * @property {string | null} detail
 * @property {number} id
 * @property {string | null} ip_address
 * @property {number} operator_id
 * @property {string} operator_name
 * @property {number | null} target_id
 * @property {string | null} target_name
 * @property {string | null} target_type
 * @typedef {Object} AuthorizeUrlResponse
 * @property {string} authorize_url
 * @property {string} state
 * @typedef {Object} BankTransferRequest
 * @property {number} amount
 * @property {string | null}= note
 * @property {string} plan_tier
 * @property {string | null}= voucher_no
 * @typedef {Object} BillingRuleCreate
 * @property {string} billing_mode
 * @property {number | null}= case_id
 * @property {string}= currency
 * @property {number | string | null}= fixed_amount
 * @property {number | string | null}= hourly_rate
 * @property {string} name
 * @typedef {Object} BindRequest
 * @property {string} app_id
 * @property {string} open_id
 * @property {string | null}= union_id
 * @typedef {Object} Body_batch_upload_documents_api_documents_batch_upload_post
 * @property {string | null}= classification
 * @property {Array<string>} files
 * @property {string | null}= knowledge_base_category
 * @property {string | null}= knowledge_base_name
 * @property {string | null}= permission_roles
 * @property {string}= permission_scope
 * @property {string | null}= permission_users
 * @property {string}= sensitivity_level
 * @property {string | null}= tags
 * @typedef {Object} Body_import_sources_api_legal_sources_import_post
 * @property {string} file
 * @typedef {Object} Body_upload_contract_review_api_legal_contract_reviews_upload_post
 * @property {string} file
 * @typedef {Object} Body_upload_document_api_documents_upload_post
 * @property {string | null}= classification
 * @property {string} file
 * @property {string | null}= knowledge_base_category
 * @property {string | null}= knowledge_base_name
 * @property {string | null}= permission_roles
 * @property {string}= permission_scope
 * @property {string | null}= permission_users
 * @property {string}= sensitivity_level
 * @property {string | null}= tags
 * @typedef {Object} Body_upload_draft_attachment_api_outbound_drafts__draft_id__attachments_post
 * @property {string} file
 * @typedef {Object} CaseMemberCreate
 * @property {string} case_role
 * @property {number} user_id
 * @typedef {Object} ChainCreateIn
 * @property {Array<ApproverIn>} approvers
 * @property {string}= chain_type
 * @property {number} target_id
 * @property {string} target_type
 * @property {number | null}= timeout_hours
 * @typedef {Object} ChatMessage
 * @property {string} content
 * @property {string} role
 * @typedef {Object} ChatRequest
 * @property {number | null}= document_id
 * @property {Array<ChatMessage>} messages
 * @typedef {Object} CompareRequest
 * @property {Array<number>} document_ids
 * @property {number}= max_length
 * @typedef {Object} ConfirmRequest
 * @property {Object | null}= invoice_snapshot
 * @property {string | null}= note
 * @typedef {Object} ConflictStatusRequest
 * @property {string | null}= resolution_note
 * @property {string} status
 * @typedef {Object} ConflictSuggestionRequest
 * @property {Array<Object>} conflicts
 * @property {Array<number>} document_ids
 * @typedef {Object} ConflictTaskConfirmRequest
 * @property {string | null}= assignee
 * @property {string | null}= priority
 * @property {string | null}= title
 * @typedef {Object} ConnectorOut
 * @property {string | null}= config_json
 * @property {string} connector_type
 * @property {string} created_at
 * @property {number | null}= department_id
 * @property {number} id
 * @property {number}= last_imported_count
 * @property {number}= last_skipped_count
 * @property {string | null}= last_sync_at
 * @property {string | null}= last_sync_status
 * @property {string} name
 * @property {number | null}= organization_id
 * @property {string} status
 * @property {number}= total_imported_count
 * @property {number}= total_skipped_count
 * @property {string} updated_at
 * @property {number} user_id
 * @typedef {Object} ConsultationIn
 * @property {number | null}= case_id
 * @property {string} question
 * @typedef {Object} ContractCompareIn
 * @property {string} content_a
 * @property {string} content_b
 * @property {string}= title_a
 * @property {string}= title_b
 * @typedef {Object} ContractCreate
 * @property {number | null}= case_id
 * @property {string | null}= contract_no
 * @property {string | null}= contract_type
 * @property {string | null}= counterparty
 * @property {string | null}= description
 * @property {string} title
 * @typedef {Object} ContractReviewIn
 * @property {number | null}= case_id
 * @property {string} content
 * @property {number | null}= document_id
 * @property {number | null}= review_policy_id
 * @property {Object | null}= review_policy_override
 * @property {string}= title
 * @typedef {Object} DeadlineCreate
 * @property {string} deadline_at
 * @property {string} deadline_type
 * @property {string | null}= description
 * @property {number}= is_historical
 * @property {number} owner_id
 * @property {string | null}= reminder_offsets_json
 * @property {string}= timezone
 * @typedef {Object} DeadlinePatch
 * @property {string | null}= action
 * @property {string | null}= deadline_at
 * @property {string | null}= description
 * @property {number | null}= owner_id
 * @property {string | null}= reminder_offsets_json
 * @typedef {Object} DepartmentCreate
 * @property {string} code
 * @property {string | null}= description
 * @property {string} name
 * @property {number} organization_id
 * @typedef {Object} DepartmentOut
 * @property {string} code
 * @property {string} created_at
 * @property {string | null}= description
 * @property {number} id
 * @property {string} name
 * @property {number} organization_id
 * @property {string} updated_at
 * @typedef {Object} DepartmentUpdate
 * @property {string | null}= description
 * @property {string | null}= name
 * @typedef {Object} DocumentBatchUploadOut
 * @property {number} count
 * @property {Array<DocumentOut>} documents
 * @typedef {Object} DocumentDownloadPolicyRequest
 * @property {boolean | null}= download_enabled
 * @property {boolean | null}= watermark_required
 * @typedef {Object} DocumentOut
 * @property {string | null}= classification
 * @property {string | null}= content_hash
 * @property {string} created_at
 * @property {number | null}= department_id
 * @property {boolean}= download_enabled
 * @property {string | null}= file_path
 * @property {string} file_type
 * @property {number} id
 * @property {number | null}= knowledge_base_id
 * @property {string | null}= metadata_json
 * @property {string | null}= object_key
 * @property {number | null}= organization_id
 * @property {number | null}= parent_document_id
 * @property {string | null}= permission_roles
 * @property {string}= permission_scope
 * @property {string | null}= permission_users
 * @property {string}= sensitivity_level
 * @property {string} status
 * @property {string | null}= summary
 * @property {string | null}= tags
 * @property {string} title
 * @property {string} updated_at
 * @property {number} user_id
 * @property {number}= version_number
 * @property {boolean}= watermark_required
 * @typedef {Object} DocumentQAFeedbackRequest
 * @property {string | null}= feedback_note
 * @property {string | null}= feedback_reason
 * @property {string} feedback_value
 * @typedef {Object} DocumentQARecordOut
 * @property {string} answer
 * @property {string | null}= citations
 * @property {string} created_at
 * @property {number} document_id
 * @property {string | null}= feedback_created_at
 * @property {string | null}= feedback_note
 * @property {string | null}= feedback_reason
 * @property {string | null}= feedback_resolution_note
 * @property {string | null}= feedback_resolved_at
 * @property {number | null}= feedback_resolved_by
 * @property {string | null}= feedback_status
 * @property {string | null}= feedback_value
 * @property {string | null}= hit_chunks
 * @property {number} id
 * @property {number | null}= latency_ms
 * @property {string | null}= model_name
 * @property {string} question
 * @property {number | null}= session_id
 * @property {string} source
 * @property {number} user_id
 * @typedef {Object} DocumentVisualAnalyzeOut
 * @property {string} analysis
 * @property {number} document_id
 * @property {string} file_type
 * @property {number}= image_count
 * @property {string} title
 * @typedef {Object} DocumentVisualAnalyzeRequest
 * @property {string}= prompt
 * @typedef {Object} DraftIn
 * @property {number | null}= case_id
 * @property {string} document_type
 * @property {Object}= fields
 * @typedef {Object} EmailSendRequestCreate
 * @property {number} smtp_connector_id
 * @typedef {Object} EmailSendRequestDecision
 * @property {boolean} approved
 * @property {string | null}= note
 * @typedef {Object} EmailSendRequestOut
 * @property {string | null}= approved_at
 * @property {number | null}= approved_by_user_id
 * @property {string | null}= approver_username
 * @property {boolean}= can_decide
 * @property {boolean}= can_execute
 * @property {string | null}= cc
 * @property {string} created_at
 * @property {Array<Object>}= dlp_findings
 * @property {string | null}= dlp_scanned_at
 * @property {string}= dlp_status
 * @property {number} draft_id
 * @property {string | null}= error_message
 * @property {number} id
 * @property {string | null}= provider_message_id
 * @property {string} recipient
 * @property {string | null}= rejection_note
 * @property {string | null}= requester_username
 * @property {string | null}= sent_at
 * @property {number} smtp_connector_id
 * @property {string} status
 * @property {string} subject
 * @property {string} updated_at
 * @property {number} user_id
 * @typedef {Object} ErrorEnvelope
 * @property {null}= data
 * @property {string | null}= detail
 * @property {null}= error
 * @property {string} message
 * @property {string} request_id
 * @property {boolean}= success
 * @property {string} trace_id
 * @typedef {Object} ExitSurveyRequest
 * @property {string | null}= feature_requests
 * @property {number | null}= nps_score
 * @property {string | null}= pain_point
 * @property {string | null}= pay_intent
 * @property {string | null}= review_wish
 * @property {string | null}= summary_feedback
 * @property {string | null}= trust_citations
 * @property {string | null}= trust_confidence
 * @property {string | null}= trust_next_steps
 * @property {string | null}= value_ranking
 * @typedef {Object} ExtractFromChatRequest
 * @property {string} message
 * @typedef {Object} ExtractFromDocRequest
 * @property {number} document_id
 * @typedef {Object} FeedbackEvalBundleRequest
 * @property {number}= days
 * @typedef {Object} FeedbackIn
 * @property {string | null}= note
 * @property {number} score
 * @typedef {Object} FollowupIn
 * @property {string} question
 * @typedef {Object} ForgotPasswordRequest
 * @property {string} email
 * @typedef {Object} HTTPValidationError
 * @property {Array<ValidationError>}= detail
 * @typedef {Object} InvoiceCreate
 * @property {string | null}= billing_period_end
 * @property {string | null}= billing_period_start
 * @property {number} case_id
 * @property {string} client_display_name
 * @property {number | string}= discount_amount
 * @property {string | null}= due_date
 * @property {string | null}= idempotency_key
 * @property {string} issue_date
 * @property {number | string}= tax_rate
 * @property {Array<number> | null}= time_entry_ids
 * @typedef {Object} JobOut
 * @property {string | null}= created_at
 * @property {string | null}= ended_at
 * @property {null}= error
 * @property {string | null}= estimated_completion
 * @property {number} job_id
 * @property {string} job_type
 * @property {number | null}= progress
 * @property {string | null}= result_summary
 * @property {number | null}= retry_count
 * @property {string | null}= started_at
 * @property {*} status
 * @property {string | null}= status_url
 * @property {number | null}= task_id
 * @typedef {Object} KnowledgeBaseCreateRequest
 * @property {string | null}= category
 * @property {string | null}= description
 * @property {string} name
 * @property {string}= permission_scope
 * @typedef {Object} KnowledgeBaseOut
 * @property {string | null}= category
 * @property {string} created_at
 * @property {number | null}= department_id
 * @property {string | null}= description
 * @property {number} id
 * @property {string} name
 * @property {number | null}= organization_id
 * @property {string} permission_scope
 * @property {string} updated_at
 * @property {number} user_id
 * @typedef {Object} LegalCaseIn
 * @property {string}= case_type
 * @property {string | null}= client_name
 * @property {string | null}= description
 * @property {boolean}= is_strict_mode
 * @property {string | null}= opposing_party
 * @property {number} organization_id
 * @property {string} title
 * @typedef {Object} LegalCaseUpdate
 * @property {string | null}= case_type
 * @property {string | null}= client_name
 * @property {string | null}= description
 * @property {boolean | null}= is_strict_mode
 * @property {string | null}= opposing_party
 * @property {string | null}= status
 * @property {string | null}= title
 * @typedef {Object} LoginLogOut
 * @property {string} created_at
 * @property {string | null} detail
 * @property {string} event_type
 * @property {number} id
 * @property {string | null} ip_address
 * @property {number | null} user_id
 * @property {string | null} username
 * @typedef {Object} LogoutRequest
 * @property {string | null}= refresh_token
 * @typedef {Object} MCPToolCallRequest
 * @property {string}= agent_type
 * @property {Object}= arguments
 * @property {string} tool_name
 * @typedef {Object} MemberInviteIn
 * @property {string}= legal_role
 * @property {number} user_id
 * @typedef {Object} MemberRoleUpdate
 * @property {string} legal_role
 * @typedef {Object} NotificationPrefUpdate
 * @property {string | null}= channels_json
 * @property {number | null}= delegate_user_id
 * @property {string | null}= mute_end
 * @property {string | null}= mute_start
 * @property {string | null}= summary_frequency
 * @property {string | null}= timezone
 * @typedef {Object} NpsRequest
 * @property {number} score
 * @typedef {Object} OAuthLoginRequest
 * @property {string} code
 * @property {string} provider
 * @typedef {Object} OnboardingUpdate
 * @property {string | null}= completed_steps_json
 * @property {string | null}= skipped_steps_json
 * @property {string | null}= user_role
 * @typedef {Object} OpenContractReviewRequest
 * @property {string} content
 * @property {string | null}= contract_type
 * @property {string | null}= idempotency_key
 * @property {number | null}= review_policy_id
 * @property {string} title
 * @typedef {Object} OrganizationCreate
 * @property {string} code
 * @property {string | null}= description
 * @property {string} name
 * @typedef {Object} OrganizationOut
 * @property {string} code
 * @property {string} created_at
 * @property {string | null}= description
 * @property {number} id
 * @property {string} name
 * @property {string} updated_at
 * @typedef {Object} OrganizationUpdate
 * @property {string | null}= description
 * @property {string | null}= name
 * @typedef {Object} OutboundEmailPolicyOut
 * @property {Array<string>}= allowed_recipient_domains
 * @property {string}= dlp_action
 * @property {boolean}= dlp_enabled
 * @property {boolean}= enabled
 * @property {number | null}= id
 * @property {number}= max_sends_per_hour
 * @property {number | null}= organization_id
 * @property {boolean}= require_approval
 * @property {string | null}= updated_at
 * @typedef {Object} OutboundEmailPolicyUpdate
 * @property {Array<string>}= allowed_recipient_domains
 * @property {string}= dlp_action
 * @property {boolean}= dlp_enabled
 * @property {boolean} enabled
 * @property {number}= max_sends_per_hour
 * @property {boolean}= require_approval
 * @typedef {Object} PagePayload
 * @property {boolean} has_next
 * @property {boolean} has_previous
 * @property {Array<*>} items
 * @property {number} page
 * @property {number} page_size
 * @property {number} total
 * @typedef {Object} PaymentCreate
 * @property {number | string} amount
 * @property {string | null}= note
 * @property {string} payment_method
 * @property {string | null}= transaction_id
 * @typedef {Object} PortalBrandingIn
 * @property {string | null}= portal_logo_url
 * @property {string | null}= portal_welcome_message
 * @typedef {Object} PortalFeedbackIn
 * @property {string | null}= note
 * @property {number} score
 * @typedef {Object} PortalLinkCreate
 * @property {number}= aggregate_case
 * @property {string} client_email
 * @property {number}= expires_days
 * @property {Array<Object>}= items
 * @property {number | null}= max_access_count
 * @property {number}= require_email_verification
 * @typedef {Object} PreferenceCreateRequest
 * @property {string}= category
 * @property {string} preference_key
 * @property {string} preference_value
 * @typedef {Object} PreferenceOut
 * @property {string} category
 * @property {string} created_at
 * @property {number} id
 * @property {string} preference_key
 * @property {string} preference_value
 * @property {string} source
 * @property {string} updated_at
 * @typedef {Object} ProgressUpdateCreate
 * @property {string} body
 * @property {string | null}= next_steps
 * @property {string} title
 * @property {string}= visibility
 * @typedef {Object} PromptTemplateCreate
 * @property {string | null}= change_note
 * @property {string | null}= description
 * @property {string} name
 * @property {string} template
 * @property {string | null}= variables
 * @typedef {Object} PromptTemplateOut
 * @property {number | null}= active_version_id
 * @property {number | null}= active_version_number
 * @property {string | null}= change_note
 * @property {string} created_at
 * @property {string | null}= description
 * @property {number} id
 * @property {string} name
 * @property {number | null}= previous_active_version_id
 * @property {number | null}= previous_active_version_number
 * @property {Object | null}= rollout
 * @property {string} template
 * @property {string} updated_at
 * @property {string | null}= variables
 * @property {Array<Object>}= variables_schema
 * @property {Array<PromptTemplateVersionOut>}= versions
 * @typedef {Object} PromptTemplateVersionOut
 * @property {string | null}= change_note
 * @property {string} created_at
 * @property {Array<Object>}= experiment_refs
 * @property {number} id
 * @property {boolean} is_active
 * @property {boolean}= is_rollout
 * @property {string} template
 * @property {number} template_id
 * @property {number}= traffic_percentage
 * @property {string} updated_at
 * @property {Array<Object>}= variables_schema
 * @property {number} version
 * @typedef {Object} RefreshTokenRequest
 * @property {string} refresh_token
 * @typedef {Object} RefundCreate
 * @property {number | string} amount
 * @property {number | null}= payment_record_id
 * @property {string} reason
 * @typedef {Object} RefundRequest
 * @property {number | string} amount
 * @property {string | null}= note
 * @typedef {Object} RegisterWithCodeRequest
 * @property {string} code
 * @property {string} email
 * @property {string | null}= full_name
 * @property {string} password
 * @property {string} username
 * @typedef {Object} RenderRequest
 * @property {Object} variables
 * @typedef {Object} ResetPasswordConfirmRequest
 * @property {string} new_password
 * @property {string} token
 * @typedef {Object} ResolveFeedbackRequest
 * @property {string | null}= resolution_note
 * @typedef {Object} RetrievalTestIn
 * @property {string} question
 * @typedef {Object} RetryTaskRunRequest
 * @property {string} source
 * @property {string} task_key
 * @typedef {Object} ReviewActionIn
 * @property {string} action
 * @property {string | null}= note
 * @typedef {Object} ReviewCommentIn
 * @property {string} note
 * @typedef {Object} ReviewPolicyCreate
 * @property {string | null}= contract_type
 * @property {string | null}= focus_points
 * @property {string} name
 * @property {string}= party_role
 * @property {string | null}= required_clauses_json
 * @property {string}= risk_preference
 * @property {string | null}= scenario
 * @typedef {Object} RiskActionIn
 * @property {string} action
 * @property {string | null}= note
 * @typedef {Object} RollbackVersionRequest
 * @property {number | null}= target_version_id
 * @typedef {Object} RolloutVersionRequest
 * @property {number} rollout_percentage
 * @property {number} version_id
 * @typedef {Object} SendVerifyCodeRequest
 * @property {string} email
 * @property {string}= purpose
 * @typedef {Object} SessionMemoryOut
 * @property {number} session_id
 * @property {number | null}= summarized_through_message_id
 * @property {string | null}= summary
 * @property {string | null}= updated_at
 * @typedef {Object} SignRequestCreate
 * @property {number} contract_version_id
 * @property {string | null}= deadline_at
 * @property {Array<*>} parties
 * @typedef {Object} SmtpConnectorCreateRequest
 * @property {string} from_address
 * @property {string} host
 * @property {string} name
 * @property {string} password
 * @property {number}= port
 * @property {boolean}= use_starttls
 * @property {string} username
 * @typedef {Object} SourceCreateIn
 * @property {Array<number>}= amended_by
 * @property {Array<number>}= amends
 * @property {string | null}= citation
 * @property {string} content
 * @property {string | null}= document_number
 * @property {string | null}= full_text
 * @property {string}= jurisdiction
 * @property {Array<string>}= keywords
 * @property {Array<string>}= law_areas
 * @property {string | null}= promulgator
 * @property {string} source_type
 * @property {string}= status
 * @property {string} title
 * @property {string}= version
 * @typedef {Object} SourceStatusUpdateIn
 * @property {string} status
 * @typedef {Object} SourceUpdateIn
 * @property {Array<number>}= amended_by
 * @property {Array<number>}= amends
 * @property {string | null}= citation
 * @property {string} content
 * @property {string | null}= document_number
 * @property {string | null}= full_text
 * @property {string}= jurisdiction
 * @property {Array<string>}= keywords
 * @property {Array<string>}= law_areas
 * @property {string | null}= promulgator
 * @property {string} source_type
 * @property {string} status
 * @property {string} title
 * @property {string}= version
 * @typedef {Object} SuccessEnvelope
 * @property {null}= data
 * @property {null}= error
 * @property {string} message
 * @property {string} request_id
 * @property {boolean}= success
 * @property {string} trace_id
 * @typedef {Object} SummarizeRequest
 * @property {boolean}= async_mode
 * @property {number}= max_length
 * @typedef {Object} TaskCommentCreate
 * @property {string} content
 * @typedef {Object} TaskCommentOut
 * @property {string} content
 * @property {string} created_at
 * @property {number} id
 * @property {number} task_id
 * @property {number} user_id
 * @typedef {Object} TaskCreate
 * @property {string | null}= assignee
 * @property {Array<string>}= collaborators
 * @property {string | null}= description
 * @property {string | null}= due_date
 * @property {string}= priority
 * @property {number}= progress
 * @property {string} title
 * @typedef {Object} TaskLogOut
 * @property {string} action
 * @property {string} created_at
 * @property {string | null}= detail
 * @property {number} id
 * @property {number} task_id
 * @typedef {Object} TaskOut
 * @property {string | null}= assignee
 * @property {Array<string>}= collaborators
 * @property {string} created_at
 * @property {number | null}= department_id
 * @property {string | null}= description
 * @property {string | null}= due_date
 * @property {number} id
 * @property {number | null}= organization_id
 * @property {number | null}= parent_id
 * @property {string}= priority
 * @property {number}= progress
 * @property {number | null}= source_id
 * @property {string | null}= source_type
 * @property {string} status
 * @property {string} title
 * @property {string} updated_at
 * @property {number} user_id
 * @typedef {Object} TaskUpdate
 * @property {string | null}= assignee
 * @property {Array<string> | null}= collaborators
 * @property {string | null}= description
 * @property {string | null}= due_date
 * @property {string | null}= priority
 * @property {number | null}= progress
 * @property {string | null}= status
 * @property {string | null}= title
 * @typedef {Object} TimeEntryCreate
 * @property {number | null}= billing_rule_id
 * @property {number} case_id
 * @property {string} description
 * @property {number | null}= duration_minutes
 * @property {string | null}= ended_at
 * @property {string | null}= idempotency_key
 * @property {string | null}= started_at
 * @typedef {Object} TimeEntryPatch
 * @property {string | null}= action
 * @property {number | null}= billable
 * @property {string | null}= description
 * @property {string | null}= ended_at
 * @typedef {Object} ToolCallLogOut
 * @property {string | null}= action_type
 * @property {number} agent_run_id
 * @property {string} created_at
 * @property {number | null}= duration_ms
 * @property {string | null}= error
 * @property {number} id
 * @property {string | null}= input_params
 * @property {string | null}= observation
 * @property {string | null}= output_result
 * @property {string | null}= raw_decision
 * @property {string} status
 * @property {number | null}= step
 * @property {string | null}= thought
 * @property {string} tool_name
 * @typedef {Object} UserCreate
 * @property {number | null}= department_id
 * @property {string} email
 * @property {string | null}= employee_id
 * @property {string | null}= full_name
 * @property {string | null}= job_title
 * @property {number | null}= organization_id
 * @property {string} password
 * @property {string} username
 * @typedef {Object} UserDetailOut
 * @property {string} created_at
 * @property {number | null}= department_id
 * @property {string} email
 * @property {string | null}= employee_id
 * @property {string | null}= external_provider
 * @property {string | null}= full_name
 * @property {number} id
 * @property {string | null}= job_title
 * @property {string | null}= last_login_at
 * @property {string | null}= last_login_ip
 * @property {string | null}= locked_until
 * @property {number}= login_fail_count
 * @property {number | null}= organization_id
 * @property {string} role
 * @property {string} status
 * @property {string | null}= updated_at
 * @property {string} username
 * @typedef {Object} UserListOut
 * @property {number | null} department_id
 * @property {string} email
 * @property {string | null} full_name
 * @property {number} id
 * @property {string | null} job_title
 * @property {string | null} last_login_at
 * @property {number | null} organization_id
 * @property {string} role
 * @property {string} status
 * @property {string} username
 * @typedef {Object} UserLogin
 * @property {string} password
 * @property {string} username
 * @typedef {Object} UserOrgAssignRequest
 * @property {number | null}= department_id
 * @property {string | null}= job_title
 * @property {number | null}= organization_id
 * @typedef {Object} UserPasswordReset
 * @property {string} new_password
 * @typedef {Object} UserRoleUpdate
 * @property {string} role
 * @typedef {Object} UserStatusUpdate
 * @property {string} status
 * @typedef {Object} ValidationError
 * @property {Object}= ctx
 * @property {*}= input
 * @property {Array<string | number>} loc
 * @property {string} msg
 * @property {string} type
 * @typedef {Object} VerifyCodeSentResponse
 * @property {string} email
 * @property {number} expires_minutes
 * @typedef {Object} VerifyEmailRequest
 * @property {string} code
 * @property {string} email
 * @property {string}= purpose
 * @typedef {Object} VersionCreate
 * @property {number | null}= source_document_id
 * @property {number | null}= source_review_id
 * @property {string}= source_type
 * @property {string | null}= text_snapshot
 * @property {string | null}= version_note
 * @typedef {Object} WechatLoginUrlResponse
 * @property {string} login_url
 * @property {string} state
 */
