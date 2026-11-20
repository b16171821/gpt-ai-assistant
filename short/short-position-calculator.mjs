export const defaults={currency:'TWD',route:'MARGIN_SHORT',capital:1000000,entry:'',stop:'',target:'',risk:1,exposure:20,lot:1000,cost:.5,slippage:.15,borrow:10,days:20,margin:'',dividend:0};
export function calculateShortPosition(input){
  const p={...defaults,...input};
  if(!['TWD','USD'].includes(p.currency))throw Error('請選擇新台幣或美元，幣別不得混算。');
  if(p.currency==='USD'&&p.route!=='US_SHORT'||p.currency==='TWD'&&!['MARGIN_SHORT','SBL'].includes(p.route))throw Error('交易途徑與資金幣別不一致，請先切換幣別。');
  const keys=['capital','entry','stop','target','risk','exposure','lot','cost','slippage','borrow','days','dividend'];
  for(const key of keys){if(p[key]===''||p[key]===null||p[key]===undefined)throw Error('請填妥價位、成本及借券假設；未知資料不會自動補成 0。');p[key]=Number(p[key]);if(!Number.isFinite(p[key])||p[key]<0)throw Error('請填入有效的非負數值。');}
  if(p.capital<=0||p.entry<=0||p.stop<=0||p.target<=0)throw Error('資金與價格必須大於 0。');
  if(!(p.stop>p.entry&&p.entry>p.target))throw Error('放空價位必須符合：停損價 > 進場價 > 目標價 > 0。');
  if(p.currency==='TWD'&&p.risk>1)throw Error('台股每筆計畫風險不得超過總資金的 1%；此限制不保證跳空後實際損失。');
  if(p.risk<=0||p.risk>100||p.exposure>100)throw Error('風險比例需大於 0、且不超過 100%；名目曝險上限介於 0～100%。');
  if(!Number.isInteger(p.lot)||p.lot<1||!Number.isInteger(p.days)||p.days<1||p.days>3650)throw Error('股數單位與計費天數必須是有效正整數。');
  let margin=null;if(p.margin!==''&&p.margin!==null&&p.margin!==undefined){margin=Number(p.margin);if(!Number.isFinite(margin)||margin<=0)throw Error('保證金比例假設須大於 0，或留白表示未知。');}
  const priceRisk=p.stop-p.entry,fee=p.entry*p.cost/100,slippage=(p.entry+p.stop)*p.slippage/100,
    borrowFee=p.entry*p.borrow/100*p.days/365,dividend=p.dividend,
    totalRisk=priceRisk+fee+slippage+borrowFee+dividend,
    riskBudget=p.capital*p.risk/100,notionalBudget=p.capital*p.exposure/100,
    riskShares=Math.floor(riskBudget/totalRisk/p.lot)*p.lot,
    notionalShares=Math.floor(notionalBudget/p.entry/p.lot)*p.lot,
    shares=Math.min(riskShares,notionalShares),
    targetCosts=fee+(p.entry+p.target)*p.slippage/100+borrowFee+dividend,
    reward=p.entry-p.target-targetCosts;
  return {...p,margin,priceRisk,costRiskPerShare:totalRisk-priceRisk,totalRiskPerShare:totalRisk,feePerShare:fee,slippagePerShare:slippage,
    borrowFeePerShare:borrowFee,riskBudget,notionalBudget,riskShares,notionalShares,shares,
    notional:shares*p.entry,estimatedLoss:shares*totalRisk,estimatedReward:shares*reward,
    theoryRR:(p.entry-p.target)/priceRisk,netRR:reward/totalRisk,rewardPct:(p.entry-p.target)/p.entry*100,riskPct:priceRisk/p.entry*100,
    estimatedMargin:margin===null?null:shares*p.entry*margin/100,
    actualOrderShares:null,executionStatus:'BROKER_CONFIRMATION_REQUIRED',
    binding:riskShares<=notionalShares?'RISK':'NOTIONAL',
    zeroReason:shares===0?(p.exposure===0?'名目曝險上限為 0%，目前不規劃新部位。':'風險或名目曝險預算不足一個股數單位。'):''};
}
