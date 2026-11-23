package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/gopcua/opcua"
	"github.com/gopcua/opcua/id"
	"github.com/gopcua/opcua/ua"
)

func (s *collector) notifier(ctx context.Context, raw string) (any, error) {
	if s.sub != nil {
		s.sub.Cancel(ctx)
		s.sub = nil
		s.events = nil
	}
	s.refreshing = false
	if raw == "" {
		return true, nil
	}
	n, err := node(raw)
	if err != nil {
		return nil, err
	}
	ch := make(chan *opcua.PublishNotificationData, 256)
	sub, err := s.client.Subscribe(ctx, &opcua.SubscriptionParameters{Interval: 500 * time.Millisecond}, ch)
	if err != nil {
		return nil, err
	}
	keep := false
	defer func() {
		if !keep {
			sub.Cancel(ctx)
		}
	}()
	fields := []string{"EventType", "Time", "SourceName", "Message", "Severity", "ConditionName", "Retain"}
	filter := &ua.EventFilter{WhereClause: &ua.ContentFilter{}}
	for i, field := range fields {
		typ := uint32(id.BaseEventType)
		if i >= 5 {
			typ = id.ConditionType
		}
		filter.SelectClauses = append(filter.SelectClauses, &ua.SimpleAttributeOperand{TypeDefinitionID: ua.NewNumericNodeID(0, typ), BrowsePath: []*ua.QualifiedName{{Name: field}}, AttributeID: ua.AttributeIDValue})
	}
	req := opcua.NewMonitoredItemCreateRequestWithDefaults(n, ua.AttributeIDEventNotifier, 1)
	req.RequestedParameters.Filter = ua.NewExtensionObject(filter)
	req.RequestedParameters.QueueSize = 200
	res, err := sub.Monitor(ctx, ua.TimestampsToReturnBoth, req)
	if err != nil {
		return nil, err
	}
	if len(res.Results) != 1 {
		return nil, errors.New("Missing event monitor result")
	}
	if res.Results[0].StatusCode != ua.StatusOK {
		return nil, res.Results[0].StatusCode
	}
	s.sub = sub
	s.events = ch
	keep = true
	_, _ = s.client.Call(ctx, &ua.CallMethodRequest{ObjectID: ua.NewNumericNodeID(0, id.ConditionType), MethodID: ua.NewNumericNodeID(0, id.ConditionType_ConditionRefresh), InputArguments: []*ua.Variant{ua.MustVariant(sub.SubscriptionID)}})
	return true, nil
}
func field(v *ua.Variant) any {
	if v == nil {
		return nil
	}
	switch val := v.Value().(type) {
	case *ua.LocalizedText:
		return val.Text
	case *ua.NodeID:
		return val.String()
	case string:
		if len(val) > 1000 {
			return val[:1000]
		}
		return val
	case uint16, bool, time.Time:
		return val
	}
	return nil
}
func (s *collector) drain() ([]object, string) {
	events := []object{}
	for i := 0; i < 256; i++ {
		select {
		case msg, ok := <-s.events:
			if !ok {
				return events, "Subscription closed"
			}
			if msg.Error != nil {
				return events, msg.Error.Error()
			}
			list, ok := msg.Value.(*ua.EventNotificationList)
			if !ok {
				continue
			}
			for _, ev := range list.Events {
				f := ev.EventFields
				if len(f) < 7 {
					continue
				}
				switch field(f[0]) {
				case fmt.Sprintf("i=%d", id.RefreshStartEventType):
					s.refreshing = true
					continue
				case fmt.Sprintf("i=%d", id.RefreshEndEventType):
					s.refreshing = false
					continue
				}
				phase := "Notification"
				if s.refreshing {
					phase = "Refresh"
				}
				events = append(events, object{"phase": phase, "received": time.Now().UTC(), "time": field(f[1]), "source": field(f[2]), "text": field(f[3]), "severity": field(f[4]), "code": field(f[5]), "retain": field(f[6])})
			}
		default:
			return events, ""
		}
	}
	return events, ""
}
