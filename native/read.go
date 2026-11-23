package main

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/gopcua/opcua/id"
	"github.com/gopcua/opcua/ua"
)

func node(value string) (*ua.NodeID, error) {
	if value == "" || len(value) > 2048 {
		return nil, errors.New("Invalid NodeId")
	}
	return ua.ParseNodeID(value)
}
func name(class ua.NodeClass) string { return strings.TrimPrefix(class.String(), "NodeClass") }
func sample(v *ua.DataValue) object {
	out := object{"quality": "BadNoData", "numeric": false, "good": false, "value": nil, "type": "Null", "sourceTime": nil}
	if v == nil {
		return out
	}
	quality := fmt.Sprintf("0x%08x", uint32(v.Status))
	if code, ok := ua.StatusCodes[v.Status]; ok {
		quality = code.Name
	}
	if v.Status == ua.StatusOK {
		quality = "Good"
	}
	good := uint32(v.Status)>>30 == 0
	out["quality"] = quality
	out["good"] = good
	if !v.SourceTimestamp.IsZero() {
		out["sourceTime"] = v.SourceTimestamp.UTC()
	}
	if v.Value == nil {
		return out
	}
	out["type"] = strings.TrimPrefix(v.Value.Type().String(), "TypeID")
	if !good {
		return out
	}
	value := v.Value.Value()
	switch n := value.(type) {
	case bool, int8, uint8, int16, uint16, int32, uint32:
		out["numeric"] = true
	case int64:
		out["numeric"] = n >= -9007199254740991 && n <= 9007199254740991
	case uint64:
		out["numeric"] = n <= 9007199254740991
	case float32:
		out["numeric"] = !math.IsNaN(float64(n)) && !math.IsInf(float64(n), 0)
	case float64:
		out["numeric"] = !math.IsNaN(n) && !math.IsInf(n, 0)
	case string:
		if len(n) > 160 {
			n = n[:160]
		}
		out["value"] = n
	default:
		if v.Value.Has(ua.VariantArrayValues) {
			out["type"] = fmt.Sprint(out["type"]) + "[]"
		}
	}
	if out["numeric"] == true {
		out["value"] = value
	}
	return out
}
func (s *collector) read(ctx context.Context, nodes []string) (any, error) {
	if len(nodes) > 128 {
		return nil, errors.New("Too many channels")
	}
	if len(nodes) == 0 {
		nodes = []string{"i=2259"}
	}
	values := make([]object, 0, len(nodes))
	for start := 0; start < len(nodes); start += 16 {
		req := &ua.ReadRequest{TimestampsToReturn: ua.TimestampsToReturnBoth}
		for _, raw := range nodes[start:min(start+16, len(nodes))] {
			n, err := node(raw)
			if err != nil {
				return nil, err
			}
			req.NodesToRead = append(req.NodesToRead, &ua.ReadValueID{NodeID: n, AttributeID: ua.AttributeIDValue})
		}
		res, err := s.client.Read(ctx, req)
		if err != nil {
			return nil, err
		}
		if len(res.Results) != len(req.NodesToRead) {
			return nil, errors.New("Incomplete read response")
		}
		for _, v := range res.Results {
			values = append(values, sample(v))
		}
	}
	events, eventError := s.drain()
	return object{"values": values, "events": events, "eventError": eventError}, nil
}
func (s *collector) preview(ctx context.Context, raw string) (any, error) {
	n, err := node(raw)
	if err != nil {
		return nil, err
	}
	res, err := s.client.Node(n).Attributes(ctx, ua.AttributeIDNodeClass, ua.AttributeIDDisplayName, ua.AttributeIDValue, ua.AttributeIDEventNotifier)
	if err != nil {
		return nil, err
	}
	if len(res) != 4 {
		return nil, errors.New("Incomplete node attributes")
	}
	if res[0].Status != ua.StatusOK {
		return nil, res[0].Status
	}
	out := sample(res[2])
	out["id"] = n.String()
	out["kind"] = name(ua.NodeClass(res[0].Value.Int()))
	out["name"] = n.String()
	if v := res[1].Value; v != nil && v.LocalizedText() != nil {
		out["name"] = v.LocalizedText().Text
	}
	out["events"] = res[3].Value != nil && res[3].Value.Uint()&1 != 0
	return out, nil
}
func (s *collector) browse(ctx context.Context, raw string, offset int) (any, error) {
	if offset < 0 || offset > 65536 {
		return nil, errors.New("Invalid browse offset")
	}
	n, err := node(raw)
	if err != nil {
		return nil, err
	}
	desc := &ua.BrowseDescription{NodeID: n, BrowseDirection: ua.BrowseDirectionForward, ReferenceTypeID: ua.NewNumericNodeID(0, id.HierarchicalReferences), IncludeSubtypes: true, NodeClassMask: uint32(ua.NodeClassObject | ua.NodeClassVariable | ua.NodeClassView), ResultMask: 63}
	res, err := s.client.Browse(ctx, &ua.BrowseRequest{NodesToBrowse: []*ua.BrowseDescription{desc}, RequestedMaxReferencesPerNode: 64})
	if err != nil {
		return nil, err
	}
	if len(res.Results) != 1 {
		return nil, errors.New("Incomplete browse response")
	}
	page := res.Results[0]
	rows := []object{}
	index := 0
	for {
		if page.StatusCode != ua.StatusOK {
			return nil, page.StatusCode
		}
		for _, ref := range page.References {
			if ref.NodeID == nil || ref.NodeID.NodeID == nil || ref.NodeID.ServerIndex != 0 {
				continue
			}
			if index >= offset && len(rows) < 65 {
				rows = append(rows, object{"id": ref.NodeID.NodeID.String(), "name": ref.DisplayName.Text, "kind": name(ref.NodeClass)})
			}
			index++
		}
		if len(page.ContinuationPoint) == 0 {
			break
		}
		stop := len(rows) >= 65
		next, err := s.client.BrowseNext(ctx, &ua.BrowseNextRequest{ReleaseContinuationPoints: stop, ContinuationPoints: [][]byte{page.ContinuationPoint}})
		if err != nil {
			return nil, err
		}
		if stop {
			break
		}
		if len(next.Results) != 1 {
			return nil, errors.New("Incomplete browse continuation")
		}
		page = next.Results[0]
	}
	var next any
	if len(rows) > 64 {
		next = offset + 64
		rows = rows[:64]
	}
	return object{"rows": rows, "next": next}, nil
}
