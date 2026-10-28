// 案件工作区的咨询结果提升到页面级：切 tab（咨询组件会被 v-if 卸载）后仍保留结果卡。
// 由 LegalWorkspace provide 一个 ref，LegalConsultationsTab inject 后交给 useLegalConsultations。
export const CONSULT_RESULT_KEY = Symbol('legal-consultation-result')
